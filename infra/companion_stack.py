import os
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_apigatewayv2 as apigwv2,
)
from aws_cdk import (
    aws_certificatemanager as acm,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_cognito as cognito,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_route53 as route53,
)
from aws_cdk import (
    aws_route53_targets as targets,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_scheduler as scheduler,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk import (
    aws_sqs as sqs,
)
from constructs import Construct


class CompanionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project_name: str,
        domain_name: str = "jatayulabs.com",
        enable_custom_domain: bool = True,
        # us-east-1 ACM cert ARN covering ``app.{domain_name}`` for the CloudFront
        # edge that fronts the streaming Function URL (CloudFront certs MUST be in
        # us-east-1). Same out-of-band pattern as LandingStack.
        edge_certificate_arn: str | None = None,
        profile_cache: dynamodb.ITable = None,
        turns_queue: sqs.IQueue = None,
        lead_cache: dynamodb.ITable = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        repo_root = Path(__file__).resolve().parents[1]
        image_excludes = [
            ".git",
            ".github",
            ".home",
            ".mcp-auth",
            ".venv",
            "__pycache__",
            "app/admin.py",
            "app/management",
            "app/migrations",
            "app/models",
            "app/tests.py",
            "app/views",
            "cdk.out",
            "config",
            "db.sqlite3",
            "deploy",
            "env",
            "frontend/node_modules",
            "infra/cdk.out",
            "node_modules",
            "staticfiles",
            "tests",
            "venv",
        ]

        # ── Cognito User Pool ──────────────────────────────────────────

        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"{project_name}-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True, username=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
                temp_password_validity=Duration.days(7),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )

        user_pool_client = cognito.UserPoolClient(
            self,
            "UserPoolClient",
            user_pool=user_pool,
            user_pool_client_name=f"{project_name}-api",
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            generate_secret=False,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True,
                    implicit_code_grant=False,
                ),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[
                    "http://localhost:8000/api/auth/callback",
                    f"https://app.{domain_name}/api/auth/callback",
                ],
            ),
            prevent_user_existence_errors=True,
        )

        user_pool_domain = cognito.UserPoolDomain(
            self,
            "UserPoolDomain",
            user_pool=user_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"{project_name}-{self.account}-auth"
            ),
        )

        families = self._table(
            "FamiliesTable",
            f"{project_name}-families",
            partition_key="family_id",
        )
        members = self._table(
            "MembersTable",
            f"{project_name}-members",
            partition_key="member_id",
        )
        # Per-user MCP connection version stamp — bumped on every connect/disconnect so every
        # Lambda container can cheaply tell when its cached "is this app connected?" answer is
        # stale (see core/providers/mcp/connection_cache.py). One tiny item per user.
        mcp_conn = self._table(
            "McpConnTable",
            f"{project_name}-mcp-conn",
            partition_key="user_id",
        )
        members.add_global_secondary_index(
            index_name="phone-number-index",
            partition_key=dynamodb.Attribute(
                name="phone_number", type=dynamodb.AttributeType.STRING
            ),
        )
        members.add_global_secondary_index(
            index_name="family-index",
            partition_key=dynamodb.Attribute(
                name="family_id", type=dynamodb.AttributeType.STRING
            ),
        )
        messages = dynamodb.Table(
            self,
            "MessagesTable",
            table_name=f"{project_name}-messages",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(
                name="member_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at_message_id", type=dynamodb.AttributeType.STRING
            ),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        reminders = dynamodb.Table(
            self,
            "RemindersTable",
            table_name=f"{project_name}-reminders",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(
                name="member_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="reminder_id", type=dynamodb.AttributeType.STRING
            ),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        app_secret = secretsmanager.Secret(
            self,
            "AppSecret",
            secret_name=f"{project_name}/app",
            description="Companion API secrets such as LLM provider keys.",
            removal_policy=RemovalPolicy.RETAIN,
        )

        scheduler_group = scheduler.CfnScheduleGroup(
            self,
            "ReminderScheduleGroup",
            name=f"{project_name}-reminders",
        )

        reminder_target_function = lambda_.DockerImageFunction(
            self,
            "ReminderTargetFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                str(repo_root),
                cmd=["companion_api.main.scheduled_reminder_handler"],
                exclude=image_excludes,
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=512,
            timeout=Duration.seconds(30),
            environment={
                "APP_SECRET_ID": app_secret.secret_name,
                "REMINDERS_TABLE": reminders.table_name,
            },
        )
        reminders.grant_read_write_data(reminder_target_function)
        app_secret.grant_read(reminder_target_function)

        scheduler_invoke_role = iam.Role(
            self,
            "SchedulerInvokeRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        reminder_target_function.grant_invoke(scheduler_invoke_role)

        # Holds the Android APK that CI rebuilds on every deploy. Private; the
        # /companion.apk route 302-redirects to a short-lived presigned URL.
        app_downloads = s3.Bucket(
            self,
            "AppDownloads",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        api_function = lambda_.DockerImageFunction(
            self,
            "ApiFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                str(repo_root),
                exclude=image_excludes,
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=1024,
            timeout=Duration.seconds(120),
            environment={
                "APP_SECRET_ID": app_secret.secret_name,
                "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
                "FAMILIES_TABLE": families.table_name,
                "MEMBERS_TABLE": members.table_name,
                "MESSAGES_TABLE": messages.table_name,
                "REMINDERS_TABLE": reminders.table_name,
                "REMINDER_TARGET_ARN": reminder_target_function.function_arn,
                "SCHEDULER_ROLE_ARN": scheduler_invoke_role.role_arn,
                "SCHEDULER_GROUP_NAME": scheduler_group.name,
                "ZOMATO_MCP_URL": "https://mcp-server.zomato.com/mcp",
                "SWIGGY_FOOD_MCP_URL": "https://mcp.swiggy.com/food",
                "EXECUTE_TOOLS_BY_DEFAULT": "false",
                "EXECUTE_ZOMATO_BY_DEFAULT": "false",
                "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                "COGNITO_CLIENT_ID": user_pool_client.user_pool_client_id,
                "COGNITO_DOMAIN": (
                    f"https://{user_pool_domain.domain_name}.auth.{cdk.Aws.REGION}.amazoncognito.com"
                ),
            },
        )
        for table in (families, members, messages, reminders, mcp_conn):
            table.grant_read_write_data(api_function)
        api_function.add_environment("MCP_CONN_TABLE", mcp_conn.table_name)
        if profile_cache:
            profile_cache.grant_read_write_data(api_function)
            api_function.add_environment("PROFILE_CACHE_TABLE", profile_cache.table_name)
        if lead_cache:
            lead_cache.grant_read_write_data(api_function)
            api_function.add_environment("LEAD_CACHE_TABLE", lead_cache.table_name)
        if turns_queue:
            turns_queue.grant_send_messages(api_function)
            api_function.add_environment("LEADGEN_QUEUE_URL", turns_queue.queue_url)
        api_function.add_environment("GRAPH_SERVICE_URL", f"https://graph.{domain_name}")
        api_function.add_environment("GRAPH_SERVICE_TOKEN_SECRET", f"{project_name}/graph-service-token")
        app_secret.grant_read(api_function)
        app_downloads.grant_read(api_function)
        api_function.add_environment("APP_DOWNLOADS_BUCKET", app_downloads.bucket_name)
        # Let the GitHub Actions deploy role upload the freshly-built APK.
        app_downloads.grant_put(
            iam.ArnPrincipal(
                f"arn:{cdk.Aws.PARTITION}:iam::{cdk.Aws.ACCOUNT_ID}:role/GitHubActionsDeployRole"
            )
        )
        cdk.CfnOutput(self, "AppDownloadsBucket", value=app_downloads.bucket_name)

        # Async agent turns: ephemeral job records (status, rolling progress,
        # final envelope) polled by the frontend; TTL reaps them after an hour.
        agent_jobs = dynamodb.Table(
            self,
            "AgentJobsTable",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        agent_jobs.grant_read_write_data(api_function)
        api_function.add_environment("AGENT_JOBS_TABLE", agent_jobs.table_name)

        # ONDC BAP transactions: per-transaction state + the async on_* callbacks
        # (one row per transaction_id + sort key); TTL reaps after a day.
        ondc_transactions = dynamodb.Table(
            self,
            "OndcTransactionsTable",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(name="transaction_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="action_message", type=dynamodb.AttributeType.STRING),
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        ondc_transactions.grant_read_write_data(api_function)
        api_function.add_environment("ONDC_TXN_TABLE", ondc_transactions.table_name)
        # The query handler re-invokes this same function (async, Event type)
        # to run tool-heavy turns past API Gateway's 30s integration cap.
        api_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:{cdk.Aws.PARTITION}:lambda:{cdk.Aws.REGION}:"
                    f"{cdk.Aws.ACCOUNT_ID}:function:{cdk.Aws.STACK_NAME}-ApiFunction*",
                ],
            )
        )

        api_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:CreateSecret",
                ],
                resources=[
                    f"arn:{cdk.Aws.PARTITION}:secretsmanager:{cdk.Aws.REGION}:"
                    f"{cdk.Aws.ACCOUNT_ID}:secret:{project_name}/*",
                ],
            )
        )
        reminder_target_function.grant_invoke(api_function)
        api_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                ],
                resources=[
                    f"arn:{cdk.Aws.PARTITION}:scheduler:{cdk.Aws.REGION}:"
                    f"{cdk.Aws.ACCOUNT_ID}:schedule/{scheduler_group.name}/*"
                ],
            )
        )
        api_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_invoke_role.role_arn],
            )
        )

        # ── Streaming Function URL (true live SSE, exactly like local) ──
        # NOTE: HTTP API Gateway was removed. The Web Adapter runs the function in
        # response_stream mode, which does NOT serve buffered API Gateway invokes
        # (they 500). Every entry now goes through the Function URL (below) fronted
        # by CloudFront, so app.* AND ondc.* share one streaming-capable path.
        # The Lambda runs uvicorn via the Web Adapter (see Dockerfile). A
        # RESPONSE_STREAM Function URL is the ONLY AWS entry that streams the
        # response body incrementally (HTTP API Gateway always buffers). Auth is
        # NONE because the app enforces its own bearer token end-to-end, and a
        # signed origin (OAC/IAM) would hijack the Authorization header. Always
        # created so dev/staging can stream against the raw URL too.
        fn_url = api_function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.ALL],
                allowed_headers=["content-type", "authorization"],
                max_age=Duration.seconds(3600),
            ),
        )
        cdk.CfnOutput(self, "ApiFunctionUrl", value=fn_url.url)

        # ── Custom domain & DNS (prod; opt-in per environment) ─────────
        # Guarded so dev/staging can deploy against the raw API Gateway URL
        # without Route53/ACM. prod (enable_custom_domain=True, the default)
        # creates the same constructs as before, so its template is unchanged.

        if enable_custom_domain:
            zone = route53.PublicHostedZone.from_lookup(
                self,
                "HostedZone",
                domain_name=domain_name,
            )

            # app.{domain} AND ondc.{domain} are both fronted by CloudFront → the
            # streaming Function URL. CloudFront's cert MUST live in us-east-1. By
            # default we provision (and DNS-validate via our own Route53 zone) a
            # dedicated cert there covering both CNAMEs — self-contained. An
            # existing us-east-1 cert ARN may be supplied to override.
            if edge_certificate_arn:
                edge_cert = acm.Certificate.from_certificate_arn(
                    self, "AppEdgeCertificate", edge_certificate_arn
                )
            else:
                edge_cert = acm.DnsValidatedCertificate(
                    self,
                    "AppEdgeCertificate",
                    domain_name=f"app.{domain_name}",
                    subject_alternative_names=[f"ondc.{domain_name}"],
                    hosted_zone=zone,
                    region="us-east-1",
                )
            # One shared origin (the streaming Function URL). Forward every viewer
            # header EXCEPT Host (the Function URL validates its own Host); never
            # cache the API. Content-hashed /assets/* are immutable → edge-cached.
            api_origin = origins.FunctionUrlOrigin(fn_url, read_timeout=Duration.seconds(60))
            asset_behavior = cloudfront.BehaviorOptions(
                origin=api_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                compress=True,
            )
            distribution = cloudfront.Distribution(
                self,
                "ApiDistribution",
                comment=f"app.{domain_name} — API + web app (streaming via LWA Function URL)",
                domain_names=[f"app.{domain_name}", f"ondc.{domain_name}"],
                certificate=edge_cert,
                price_class=cloudfront.PriceClass.PRICE_CLASS_100,
                default_behavior=cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    compress=True,
                ),
                additional_behaviors={"/assets/*": asset_behavior},
            )
            route53.ARecord(
                self,
                "ApiAliasRecord",
                zone=zone,
                record_name="app",
                target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)),
            )
            cdk.CfnOutput(
                self, "ApiCloudFrontDomain", value=distribution.distribution_domain_name
            )

            # ondc.{domain} → the SAME CloudFront distribution (it's a CNAME on the
            # distribution); the app routes ONDC by path. No API Gateway involved.
            route53.ARecord(
                self,
                "OndcAliasRecord",
                zone=zone,
                record_name="ondc",
                target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)),
            )

        # ── Outputs ────────────────────────────────────────────────────

        cdk.CfnOutput(self, "CognitoUserPoolId", value=user_pool.user_pool_id)
        cdk.CfnOutput(
            self, "CognitoClientId", value=user_pool_client.user_pool_client_id
        )
        cdk.CfnOutput(
            self,
            "CognitoDomain",
            value=f"https://{user_pool_domain.domain_name}.auth.{cdk.Aws.REGION}.amazoncognito.com",
        )
        cdk.CfnOutput(self, "AppSecretName", value=app_secret.secret_name)
        cdk.CfnOutput(self, "RemindersTableName", value=reminders.table_name)

    def _table(
        self,
        construct_id: str,
        table_name: str,
        *,
        partition_key: str,
    ) -> dynamodb.Table:
        return dynamodb.Table(
            self,
            construct_id,
            table_name=table_name,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(
                name=partition_key, type=dynamodb.AttributeType.STRING
            ),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
