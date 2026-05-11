from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_dynamodb as dynamodb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_scheduler as scheduler,
    aws_cognito as cognito,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class CompanionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project_name: str,
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
                refresh_token=True,
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
                callback_urls=["http://localhost:8000/api/auth/callback"],
            ),
            prevent_user_existence_errors=True,
        )

        user_pool_domain = cognito.UserPoolDomain(
            self,
            "UserPoolDomain",
            user_pool=user_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"{project_name}-auth"
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
                "FAMILIES_TABLE": families.table_name,
                "MEMBERS_TABLE": members.table_name,
                "MESSAGES_TABLE": messages.table_name,
                "REMINDERS_TABLE": reminders.table_name,
                "REMINDER_TARGET_ARN": reminder_target_function.function_arn,
                "SCHEDULER_ROLE_ARN": scheduler_invoke_role.role_arn,
                "SCHEDULER_GROUP_NAME": scheduler_group.name,
                "ZOMATO_MCP_URL": "https://mcp-server.zomato.com/mcp",
                "EXECUTE_TOOLS_BY_DEFAULT": "false",
                "EXECUTE_ZOMATO_BY_DEFAULT": "false",
                "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                "COGNITO_CLIENT_ID": user_pool_client.user_pool_client_id,
            },
        )
        for table in (families, members, messages, reminders):
            table.grant_read_write_data(api_function)
        app_secret.grant_read(api_function)
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

        api = apigwv2.CfnApi(
            self,
            "HttpApi",
            name=f"{project_name}-api",
            protocol_type="HTTP",
            cors_configuration=apigwv2.CfnApi.CorsProperty(
                allow_headers=["content-type", "authorization"],
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_origins=["*"],
                max_age=3600,
            ),
        )
        integration = apigwv2.CfnIntegration(
            self,
            "ApiIntegration",
            api_id=api.ref,
            integration_type="AWS_PROXY",
            integration_uri=api_function.function_arn,
            payload_format_version="2.0",
        )
        apigwv2.CfnRoute(
            self,
            "DefaultRoute",
            api_id=api.ref,
            route_key="$default",
            target=f"integrations/{integration.ref}",
        )
        apigwv2.CfnStage(
            self,
            "DefaultStage",
            api_id=api.ref,
            stage_name="$default",
            auto_deploy=True,
        )
        lambda_.CfnPermission(
            self,
            "ApiInvokePermission",
            action="lambda:InvokeFunction",
            function_name=api_function.function_name,
            principal="apigateway.amazonaws.com",
            source_arn=(
                f"arn:{cdk.Aws.PARTITION}:execute-api:{cdk.Aws.REGION}:"
                f"{cdk.Aws.ACCOUNT_ID}:{api.ref}/*/*"
            ),
        )

        # ── Outputs ────────────────────────────────────────────────────

        cdk.CfnOutput(
            self,
            "HttpApiEndpoint",
            value=f"https://{api.ref}.execute-api.{cdk.Aws.REGION}.amazonaws.com",
        )
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