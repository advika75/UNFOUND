"""UNFOUND search API: Lambda container image behind a Function URL, ap-northeast-1,
no VPC (public internet egress to Supabase; a NAT Gateway would be $32/mo we don't
want), arm64/Graviton, memory 2048 MB, timeout 30s.

Config comes from SSM Parameter Store, never as literals in this file:

  Plain String parameters, resolved from the *current* SSM value at every `cdk
  deploy` (the AWS::SSM::Parameter::Value<String> mechanism -- see PARAM_PREFIX),
  baked into the Lambda's environment as the actual value:
    /unfound/prod/SUPABASE_URL
    /unfound/prod/SUPABASE_KEY          (Supabase publishable/anon key -- not
                                          secret by Supabase's own model)
    /unfound/prod/ALLOWED_ORIGINS

  SecureString parameters -- true secrets. AWS rejects `ssm-secure` dynamic
  references inside a Lambda function's Environment.Variables entirely (confirmed
  by `cdk synth`'s own validator, not a CDK-side limitation), so CloudFormation
  can never resolve these at deploy time. Instead only the parameter *path* is
  passed as a plain env var, and backend.app._resolve_secret fetches + decrypts
  the real value once via boto3 at Lambda startup, caching it in the process (see
  backend/app.py):
    /unfound/prod/SUPABASE_SERVICE_ROLE_KEY   (only if ENABLE_PERSONALIZATION)
    /unfound/prod/ADMIN_API_KEY                (only if ENABLE_ADMIN)

None of these parameters are created by this stack -- see infra/README.md for the
exact `aws ssm put-parameter` commands to create them out of band.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

PARAM_PREFIX = "/unfound/prod"
FUNCTION_NAME = "unfound-api"


class UnfoundApiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Toggle these once the corresponding SSM SecureString actually exists --
        # a dynamic reference to a parameter that doesn't exist fails the deploy.
        enable_personalization = self.node.try_get_context("enablePersonalization") == "true"
        enable_admin = self.node.try_get_context("enableAdmin") == "true"
        image_tag = self.node.try_get_context("imageTag") or "latest"
        # A Lambda pointing at a container image can't be created until that image tag
        # already exists in the repo -- but the repo doesn't exist until this stack's
        # first deploy creates it. So the very first deploy only, run with
        # `-c deployFunction=false` to create just the repo, push an image into it
        # (infra/build_and_push.sh), then redeploy without the flag (default: true) to
        # add the Function/FunctionUrl/LogGroup/IAM. See infra/README.md.
        deploy_function = self.node.try_get_context("deployFunction") != "false"

        # ---- ECR: holds the container image; built and pushed by infra/build_and_push.sh,
        # not by this stack (keeps "provision infra" and "build/push image" independent so
        # a plain `cdk deploy` never needs a local Docker build). ----
        repository = ecr.Repository(
            self,
            "Repository",
            repository_name=FUNCTION_NAME,
            image_scan_on_push=True,
            empty_on_delete=True,  # so `cdk destroy` doesn't get stuck on a non-empty repo
            removal_policy=RemovalPolicy.DESTROY,
            lifecycle_rules=[ecr.LifecycleRule(description="keep last 10 images", max_image_count=10)],
        )

        # ---- CloudWatch log group, 14-day retention (default is "never expire") ----
        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/aws/lambda/{FUNCTION_NAME}",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        cdk.CfnOutput(self, "RepositoryUri", value=repository.repository_uri)

        if not deploy_function:
            return  # first-deploy bootstrap: repo (and log group) only, no image to point at yet

        def ssm_string(name: str) -> str:
            return ssm.StringParameter.value_for_string_parameter(self, f"{PARAM_PREFIX}/{name}")

        environment = {
            "ENVIRONMENT": "production",
            "SUPABASE_URL": ssm_string("SUPABASE_URL"),
            "SUPABASE_KEY": ssm_string("SUPABASE_KEY"),
            "ALLOWED_ORIGINS": ssm_string("ALLOWED_ORIGINS"),
        }
        # Paths, not values -- backend.app._resolve_secret fetches + decrypts these
        # itself at runtime (see module docstring above).
        if enable_personalization:
            environment["SUPABASE_SERVICE_ROLE_KEY_SSM_PARAM"] = f"{PARAM_PREFIX}/SUPABASE_SERVICE_ROLE_KEY"
        if enable_admin:
            environment["ADMIN_API_KEY_SSM_PARAM"] = f"{PARAM_PREFIX}/ADMIN_API_KEY"

        function = lambda_.DockerImageFunction(
            self,
            "Function",
            function_name=FUNCTION_NAME,
            code=lambda_.DockerImageCode.from_ecr(repository=repository, tag_or_digest=image_tag),
            architecture=lambda_.Architecture.ARM_64,
            memory_size=2048,
            timeout=Duration.seconds(30),
            environment=environment,
            log_group=log_group,
            # No vpc= -- public internet egress to Supabase, no NAT Gateway.
        )

        # Least privilege: only this app's own SSM parameters, read-only. Actually
        # exercised at runtime now -- backend.app._resolve_secret calls this for the
        # two SecureString secrets on first use.
        function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter", "ssm:GetParameters"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{PARAM_PREFIX}/*"],
            )
        )
        if enable_personalization or enable_admin:
            # SecureString decryption needs kms:Decrypt on the key SSM used to encrypt it.
            # These use the default AWS-managed key (alias/aws/ssm), whose actual key ID
            # isn't known ahead of time, so this is scoped by condition (decrypt only when
            # the call comes via SSM in this region) rather than by a specific key ARN --
            # the standard pattern for this exact situation.
            function.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["kms:Decrypt"],
                    resources=["*"],
                    conditions={"StringEquals": {"kms:ViaService": f"ssm.{self.region}.amazonaws.com"}},
                )
            )

        function_url = function.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            # No cors= here: Lambda Function URL CORS handling stays off, the app's own
            # CORSMiddleware (ALLOWED_ORIGINS) is the single source of truth for CORS.
        )

        cdk.CfnOutput(self, "FunctionUrl", value=function_url.url)
        cdk.CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
