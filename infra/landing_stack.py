from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    RemovalPolicy,
    Stack,
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
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_deployment as s3deploy,
)
from constructs import Construct


class LandingStack(Stack):
    """The static Jatayu Labs landing site (landing/) on S3 behind CloudFront.

    The ACM cert is created out-of-band in us-east-1 (CloudFront's required
    region) and passed in by ARN, so this stack stays in the workload region
    and needs no cross-region machinery. DNS (apex + www aliases to the
    distribution) is swapped over separately once the distribution is live.
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
        repo_root = Path(__file__).resolve().parents[1]

        bucket = s3.Bucket(
            self,
            "LandingBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        certificate = acm.Certificate.from_certificate_arn(self, "Cert", certificate_arn)

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True,
            ),
            domain_names=[domain_name, f"www.{domain_name}"],
            certificate=certificate,
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            comment=f"{domain_name} landing site",
        )

        s3deploy.BucketDeployment(
            self,
            "DeployLanding",
            sources=[s3deploy.Source.asset(str(repo_root / "landing"))],
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        cdk.CfnOutput(self, "DistributionDomain", value=distribution.distribution_domain_name)
        cdk.CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        cdk.CfnOutput(self, "LandingBucketName", value=bucket.bucket_name)
