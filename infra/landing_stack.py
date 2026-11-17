"""Isolated static company-site hosting. No product backend dependencies."""
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct


class LandingStack(Stack):
    """Jatayu Labs company site on private S3 and CloudFront.

    DNS cutover is a separately reviewed operation. The us-east-1 certificate
    is pre-existing and DNS-validated. This stack owns no VPC, database, or
    product services. Versioned site content is retained for rollback.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        certificate_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        root = Path(__file__).resolve().parents[1]
        csp = (root / "site" / "csp.txt").read_text().strip()
        bucket = s3.Bucket(
            self,
            "LandingBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )
        certificate = acm.Certificate.from_certificate_arn(
            self, "Cert", certificate_arn
        )
        response_headers = cloudfront.ResponseHeadersPolicy(
            self,
            "SiteSecurityHeaders",
            comment="Company site security policy; no third-party scripts or trackers",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=csp, override=True
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(
                    override=True
                ),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.SAMEORIGIN,
                    override=True,
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=False,
                    preload=False,
                    override=True,
                ),
            ),
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy",
                        value="camera=(), microphone=(), geolocation=(), payment=()",
                        override=True,
                    ),
                    cloudfront.ResponseCustomHeader(
                        header="Cross-Origin-Opener-Policy",
                        value="same-origin",
                        override=True,
                    ),
                ]
            ),
        )
        routes = cloudfront.Function(
            self,
            "SiteRoutes",
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            code=cloudfront.FunctionCode.from_inline(
                (root / "site" / "edge-routes.js").read_text()
            ),
            comment="Canonical www redirect and static directory index routing",
        )
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=response_headers,
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=routes,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            domain_names=[domain_name, f"www.{domain_name}"],
            certificate=certificate,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_root_object="index.html",
            enable_ipv6=True,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            comment=f"{domain_name} company site",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=code,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.seconds(30),
                )
                for code in (403, 404)
            ],
        )
        s3deploy.BucketDeployment(
            self,
            "DeployLanding",
            sources=[s3deploy.Source.asset(str(root / "landing"))],
            destination_bucket=bucket,
            prune=False,
            retain_on_delete=True,
            cache_control=[s3deploy.CacheControl.from_string("public,max-age=300")],
        )
        cdk.CfnOutput(self, "DistributionDomain", value=distribution.distribution_domain_name)
        cdk.CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        cdk.CfnOutput(self, "LandingBucketName", value=bucket.bucket_name)
