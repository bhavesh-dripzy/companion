from pathlib import Path
import os
import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_sqs as sqs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_secretsmanager as secretsmanager,
    aws_s3_assets as s3_assets,
)
from constructs import Construct


class GraphStack(Stack):
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

        # 1. SQS Queue for Leadgen Turns
        dlq = sqs.Queue(
            self,
            "LeadgenTurnsDlq",
            queue_name=f"{project_name}-leadgen-turns-dlq",
            retention_period=Duration.days(14),
        )

        self.turns_queue = sqs.Queue(
            self,
            "LeadgenTurnsQueue",
            queue_name=f"{project_name}-leadgen-turns",
            visibility_timeout=Duration.seconds(120),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=5,
                queue=dlq,
            ),
        )

        # 2. DynamoDB Profile Cache Table
        self.profile_cache = dynamodb.Table(
            self,
            "ProfileCacheTable",
            table_name=f"{project_name}-profile-cache",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 3. DynamoDB Lead Cache Table
        self.lead_cache = dynamodb.Table(
            self,
            "LeadCacheTable",
            table_name=f"{project_name}-lead-cache",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            partition_key=dynamodb.Attribute(
                name="lead_id", type=dynamodb.AttributeType.STRING
            ),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.lead_cache.add_global_secondary_index(
            index_name="category-status-index",
            partition_key=dynamodb.Attribute(
                name="category", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="status_last_seen", type=dynamodb.AttributeType.STRING
            ),
        )

        # 3. Secrets
        neo4j_secret = secretsmanager.Secret(
            self,
            "Neo4jSecret",
            secret_name=f"{project_name}/neo4j",
            description="Neo4j credentials for the graph database",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "neo4j"}',
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=16,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        graph_service_secret = secretsmanager.Secret(
            self,
            "GraphServiceSecret",
            secret_name=f"{project_name}/graph-service",
            description="FastAPI graph service bearer token",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{}',
                generate_string_key="token",
                exclude_punctuation=True,
                password_length=32,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 4. App Code Asset (Upload workspace code to S3)
        asset = s3_assets.Asset(
            self,
            "AppCodeAsset",
            path=str(repo_root),
            exclude=[
                ".git",
                ".github",
                ".venv",
                "node_modules",
                "cdk.out",
                "infra/cdk.out",
                "__pycache__",
                "*.pyc",
                ".pytest_cache",
            ],
        )

        # 5. Networking (Lookup Default VPC and Route53 zone)
        deploy_ec2 = os.getenv("DEPLOY_EC2", "false").strip().lower() == "true"
        if not deploy_ec2:
            return

        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        zone = route53.PublicHostedZone.from_lookup(
            self,
            "HostedZone",
            domain_name="houzzat.in",
        )

        sg = ec2.SecurityGroup(
            self,
            "GraphSecurityGroup",
            vpc=vpc,
            description="Allow HTTP/HTTPS traffic",
            allow_all_outbound=True,
        )
        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP (for Caddy redirects and challenges)",
        )
        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from anywhere (graph service)",
        )

        # 7. IAM Role for EC2
        role = iam.Role(
            self,
            "GraphEc2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ],
        )

        # Grant access to SQS, S3 Asset, DynamoDB Cache, and Lead Cache
        self.turns_queue.grant_consume_messages(role)
        self.profile_cache.grant_read_write_data(role)
        self.lead_cache.grant_read_write_data(role)
        asset.grant_read(role)

        # Grant access to Messages table (by physical name prefix)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                ],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{project_name}-messages",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{project_name}-messages/index/*",
                ],
            )
        )

        # Grant access to application secrets
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{project_name}/*",
                ],
            )
        )

        # Grant access to Route 53 (if Caddy uses DNS challenges)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "route53:ChangeResourceRecordSets",
                    "route53:ListResourceRecordSets",
                ],
                resources=[
                    f"arn:aws:route53:::hostedzone/{zone.hosted_zone_id}"
                ],
            )
        )

        # 8. User Data (Shell Script to run at boot)
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "echo '--- Starting Bootstrap ---'",
            "dnf update -y",
            "dnf install -y docker python3 python3-pip git unzip gcc python3-devel",
            
            # Start Docker
            "systemctl enable --now docker",
            
            # Fetch Neo4j Password from Secrets Manager
            f"NEO4J_SECRET=$(aws secretsmanager get-secret-value --secret-id {project_name}/neo4j --region {self.region} --query SecretString --output text)",
            "NEO4J_PASSWORD=$(python3 -c \"import json; print(json.loads('''$NEO4J_SECRET''')['password'])\")",
            
            # Run Neo4j Docker container on localhost (Bolt 7687, Browser 7474)
            "mkdir -p /var/lib/neo4j/data /var/lib/neo4j/logs",
            "docker run -d \\"
            "  --name neo4j \\"
            "  -p 127.0.0.1:7687:7687 \\"
            "  -p 127.0.0.1:7474:7474 \\"
            "  -v /var/lib/neo4j/data:/data \\"
            "  -v /var/lib/neo4j/logs:/logs \\"
            "  --env NEO4J_AUTH=neo4j/$NEO4J_PASSWORD \\"
            "  --restart always \\"
            "  neo4j:5-community",
            
            # Download and extract application code from S3 Asset
            "mkdir -p /var/www/companion",
            f"aws s3 cp s3://{asset.s3_bucket_name}/{asset.s3_object_key} /tmp/app.zip",
            "unzip -o /tmp/app.zip -d /var/www/companion",
            
            # Setup Virtual Environment and install requirements
            "python3 -m venv /var/www/companion/venv",
            "/var/www/companion/venv/bin/pip install --upgrade pip",
            "/var/www/companion/venv/bin/pip install -r /var/www/companion/requirements.txt",
            "/var/www/companion/venv/bin/pip install uvicorn fastapi neo4j pydantic pydantic-settings cryptography",
            
            # Configure Caddy Repository and Install Caddy
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/setup.rpm.sh' | bash",
            "dnf install -y caddy",
            
            # Setup graph-service environment and systemd services
            "mkdir -p /etc/companion",
            f"cat <<EOF > /etc/companion/env\n"
            f"AWS_REGION={self.region}\n"
            f"PROFILE_CACHE_TABLE={project_name}-profile-cache\n"
            f"MESSAGES_TABLE={project_name}-messages\n"
            f"LEADGEN_QUEUE_URL={self.turns_queue.queue_url}\n"
            f"PROJECT_NAME={project_name}\n"
            f"EOF",
            
            # Systemd Service for fastapi graph-service
            "cat <<EOF > /etc/systemd/system/graph-service.service\n"
            "[Unit]\n"
            "Description=FastAPI Knowledge Graph Service\n"
            "After=network.target docker.service\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "User=root\n"
            "WorkingDirectory=/var/www/companion\n"
            "EnvironmentFile=/etc/companion/env\n"
            "ExecStart=/var/www/companion/venv/bin/uvicorn deploy.graph-ec2.graph_service:app --host 127.0.0.1 --port 8000\n"
            "Restart=always\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
            "EOF",
            
            # Systemd Service for leadgen-worker
            "cat <<EOF > /etc/systemd/system/leadgen-worker.service\n"
            "[Unit]\n"
            "Description=Leadgen Extraction SQS Worker\n"
            "After=network.target docker.service graph-service.service\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "User=root\n"
            "WorkingDirectory=/var/www/companion\n"
            "EnvironmentFile=/etc/companion/env\n"
            "ExecStart=/var/www/companion/venv/bin/python3 deploy/graph-ec2/worker.py\n"
            "Restart=always\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
            "EOF",
            
            # Caddyfile setup
            "cat <<EOF > /etc/caddy/Caddyfile\n"
            "graph.houzzat.in {\n"
            "    reverse_proxy 127.0.0.1:8000\n"
            "}\n"
            "EOF",
            
            # Start services
            "systemctl daemon-reload",
            "systemctl enable --now graph-service",
            "systemctl enable --now leadgen-worker",
            "systemctl enable --now caddy",
            "echo '--- Bootstrap Completed ---'",
        )

        # 9. EC2 Instance
        instance = ec2.Instance(
            self,
            "GraphInstance",
            instance_type=ec2.InstanceType("t4g.small"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            ),
            security_group=sg,
            role=role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=30,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        encrypted=True,
                    ),
                )
            ],
        )

        # 10. Elastic IP (EIP)
        eip = ec2.CfnEIP(
            self,
            "GraphEip",
            domain="vpc",
            instance_id=instance.instance_id,
        )

        # 11. DNS A Record pointing to the Elastic IP
        route53.ARecord(
            self,
            "GraphAliasRecord",
            zone=zone,
            record_name="graph",
            target=route53.RecordTarget.from_ip_addresses(eip.ref),
        )
