#!/usr/bin/env python3
import os

import aws_cdk as cdk

from companion_stack import CompanionStack
from graph_stack import GraphStack
from landing_stack import LandingStack


app = cdk.App()
project_name = app.node.try_get_context("projectName") or "companion"
region = os.getenv("CDK_DEFAULT_REGION") or app.node.try_get_context("defaultRegion")
account = os.getenv("CDK_DEFAULT_ACCOUNT")

# Environment selector. `prod` (the default) keeps the original stack names,
# resource names, domain, and construct IDs so the live stacks synthesize
# unchanged. `dev`/`staging` get isolated, prefixed resources and (by default)
# deploy without the custom domain so they need no Route53/ACM/DNS.
env_name = (
    app.node.try_get_context("environmentName") or os.getenv("COMPANION_ENV") or "prod"
).strip().lower()
is_prod = env_name == "prod"
resource_prefix = project_name if is_prod else f"{project_name}-{env_name}"
stack_prefix = project_name if is_prod else f"{project_name}-{env_name}"
domain_name = "jatayulabs.com" if is_prod else f"{env_name}.jatayulabs.com"

env = cdk.Environment(account=account, region=region)

graph_stack = GraphStack(
    app,
    f"{stack_prefix}-graph",
    project_name=resource_prefix,
    domain_name=domain_name,
    enable_custom_domain=is_prod,
    env=env,
)

CompanionStack(
    app,
    f"{stack_prefix}-serverless",
    project_name=resource_prefix,
    domain_name=domain_name,
    enable_custom_domain=is_prod,
    # us-east-1 ACM cert for the CloudFront edge that fronts app.jatayulabs.com →
    # the streaming Function URL. Must cover app.{domain} (a *.jatayulabs.com
    # wildcard cert does). Only needed for prod; overridable via EDGE_CERTIFICATE_ARN.
    edge_certificate_arn=(
        (
            os.getenv("EDGE_CERTIFICATE_ARN")
            or "arn:aws:acm:us-east-1:616551057455:certificate/489a052c-a6a5-437a-9763-2b50a684a394"
        )
        if is_prod
        else None
    ),
    profile_cache=graph_stack.profile_cache,
    turns_queue=graph_stack.turns_queue,
    lead_cache=graph_stack.lead_cache,
    env=env,
)

# Apex landing site (prod): jatayulabs.com on S3 + CloudFront. The ACM cert
# lives in us-east-1 (CloudFront's required region) and is referenced by ARN.
if is_prod:
    LandingStack(
        app,
        f"{stack_prefix}-landing",
        domain_name=domain_name,
        certificate_arn="arn:aws:acm:us-east-1:616551057455:certificate/489a052c-a6a5-437a-9763-2b50a684a394",
        env=env,
    )

app.synth()
