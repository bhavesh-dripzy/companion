"""Allow only the isolated website resource types before a deployment."""
import json
import sys
from pathlib import Path

ALLOWED = {
    "AWS::S3::Bucket",
    "AWS::S3::BucketPolicy",
    "AWS::CloudFront::Distribution",
    "AWS::CloudFront::OriginAccessControl",
    "AWS::CloudFront::ResponseHeadersPolicy",
    "AWS::CloudFront::Function",
    "AWS::IAM::Role",
    "AWS::IAM::Policy",
    "AWS::Lambda::Function",
    "AWS::Lambda::LayerVersion",
    "AWS::CDK::Metadata",
    "Custom::CDKBucketDeployment",
}

template = json.loads(Path(sys.argv[1]).read_text())
resources = template["Resources"]
for name, resource in resources.items():
    kind = resource["Type"]
    assert kind in ALLOWED, f"Rejected non-website resource: {name} ({kind})"
    if kind == "AWS::Lambda::LayerVersion":
        assert name.startswith("DeployLandingAwsCliLayer"), name
    if kind == "AWS::Lambda::Function":
        assert "CustomCDKBucketDeployment" in name, name
    if kind in {"AWS::IAM::Role", "AWS::IAM::Policy"}:
        assert "CustomCDKBucketDeployment" in name, name
    if kind == "AWS::S3::Bucket":
        assert resource["DeletionPolicy"] == "Retain", name
        assert resource["Properties"]["VersioningConfiguration"]["Status"] == "Enabled"
        assert all(resource["Properties"]["PublicAccessBlockConfiguration"].values())
    if kind == "AWS::CloudFront::Distribution":
        config = resource["Properties"]["DistributionConfig"]
        assert sorted(config["Aliases"]) == ["jatayulabs.com", "www.jatayulabs.com"]
        assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
        assert config["ViewerCertificate"]["MinimumProtocolVersion"] == "TLSv1.2_2021"
print(f"Website-only template verified: {len(resources)} resources. No network, database, or product stacks.")
