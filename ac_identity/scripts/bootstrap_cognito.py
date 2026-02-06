import os, json, time, secrets, string
from dotenv import load_dotenv
import boto3

load_dotenv()

REGION = os.getenv("COGNITO_REGION", os.getenv("AWS_REGION", "us-east-1"))
PREFIX = os.getenv("COGNITO_PREFIX", "agentcore-id-e2e")
USERNAME = os.getenv("COGNITO_USERNAME", f"agentcoretester+{int(time.time())}@example.com")
PASSWORD = os.getenv("COGNITO_PASSWORD", "T3st!" + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14)))

cognito = boto3.client("cognito-idp", region_name=REGION)

pool_name = f"{PREFIX}-pool-{int(time.time())}"
pool = cognito.create_user_pool(
    PoolName=pool_name,
    Policies={"PasswordPolicy":{
        "MinimumLength": 12,
        "RequireUppercase": True,
        "RequireLowercase": True,
        "RequireNumbers": True,
        "RequireSymbols": True
    }},
    AutoVerifiedAttributes=["email"],
    UsernameAttributes=["email"]
)
user_pool_id = pool["UserPool"]["Id"]

client = cognito.create_user_pool_client(
    UserPoolId=user_pool_id,
    ClientName=f"{PREFIX}-client",
    GenerateSecret=False,
    ExplicitAuthFlows=[
        "ALLOW_USER_PASSWORD_AUTH",
        "ALLOW_REFRESH_TOKEN_AUTH",
        "ALLOW_USER_SRP_AUTH"
    ],
    PreventUserExistenceErrors="ENABLED"
)
client_id = client["UserPoolClient"]["ClientId"]

cognito.admin_create_user(
    UserPoolId=user_pool_id,
    Username=USERNAME,
    UserAttributes=[{"Name":"email","Value":USERNAME},{"Name":"email_verified","Value":"true"}],
    TemporaryPassword=PASSWORD,
    MessageAction="SUPPRESS"
)

cognito.admin_set_user_password(
    UserPoolId=user_pool_id,
    Username=USERNAME,
    Password=PASSWORD,
    Permanent=True
)

out = {
    "COGNITO_REGION": REGION,
    "COGNITO_USER_POOL_ID": user_pool_id,
    "COGNITO_CLIENT_ID": client_id,
    "COGNITO_USERNAME": USERNAME,
    "COGNITO_PASSWORD": PASSWORD
}
print(json.dumps(out, indent=2))

with open(".cognito_bootstrap.json", "w") as f:
    json.dump(out, f, indent=2)
print("Wrote .cognito_bootstrap.json")
