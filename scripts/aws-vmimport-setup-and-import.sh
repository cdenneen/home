#!/usr/bin/env bash
set -euo pipefail

# Creates/updates the IAM role required for EC2 VM Import/Export (ImportSnapshot)
# and then kicks off an import-snapshot task for a VHD in S3.
#
# Usage:
#   scripts/aws-vmimport-setup-and-import.sh \
#     --s3-key "ami/nyx/20260205-201337/amazon-ami.vhd" \
#     --host nyx \
#     --region us-east-1

REGION="us-east-1"
BUCKET="chris-denneen-cloud9"
HOST="nyx"
S3_KEY=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --region)
      REGION="$2"; shift 2;;
    --bucket)
      BUCKET="$2"; shift 2;;
    --host)
      HOST="$2"; shift 2;;
    --s3-key)
      S3_KEY="$2"; shift 2;;
    -h|--help)
      sed -n '1,40p' "$0"; exit 0;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2;;
  esac
done

if [ -z "$S3_KEY" ]; then
  echo "Missing --s3-key" >&2
  exit 2
fi

DATE="$(date +%Y%m%d-%H%M%S)"
if [[ "$S3_KEY" =~ /([0-9]{8}-[0-9]{6})/ ]]; then
  DATE="${BASH_REMATCH[1]}"
fi

echo "Account: $(aws sts get-caller-identity --query Account --output text)"
echo "Caller:  $(aws sts get-caller-identity --query Arn --output text)"
echo "Region:  $REGION"
echo "Bucket:  $BUCKET"
echo "S3 key:  $S3_KEY"
echo "Host:    $HOST"
echo "Date:    $DATE"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

cat >"$tmpdir/vmimport-trust.json" <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "VMImportExportTrust",
      "Effect": "Allow",
      "Principal": { "Service": "vmie.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

cat >"$tmpdir/vmimport-policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3ReadForImport",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}"
    },
    {
      "Sid": "S3GetObjectForImport",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}/ami/*"
    },
    {
      "Sid": "KmsDecryptForS3Objects",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:DescribeKey",
        "kms:GenerateDataKey"
      ],
      "Resource": "arn:aws:kms:${REGION}:838870929816:key/*"
    },
    {
      "Sid": "EBSImportSnapshot",
      "Effect": "Allow",
      "Action": [
        "ec2:ImportSnapshot",
        "ec2:ModifySnapshotAttribute",
        "ec2:CopySnapshot",
        "ec2:RegisterImage",
        "ec2:Describe*"
      ],
      "Resource": "*"
    }
  ]
}
EOF

if aws iam get-role --role-name vmimport >/dev/null 2>&1; then
  echo "Updating existing role: vmimport"
  aws iam update-assume-role-policy --role-name vmimport --policy-document "file://$tmpdir/vmimport-trust.json" >/dev/null
else
  echo "Creating role: vmimport"
  aws iam create-role --role-name vmimport --assume-role-policy-document "file://$tmpdir/vmimport-trust.json" >/dev/null
fi

echo "Attaching/refreshing inline policy: vmimport"
aws iam put-role-policy --role-name vmimport --policy-name vmimport --policy-document "file://$tmpdir/vmimport-policy.json" >/dev/null

# If the bucket uses SSE-KMS by default, grant the vmimport role decrypt rights.
KMS_KEY_ARN=$(aws s3api get-bucket-encryption --bucket "$BUCKET" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.KMSMasterKeyID' --output text 2>/dev/null || true)
if [ -n "$KMS_KEY_ARN" ] && [ "$KMS_KEY_ARN" != "None" ] && [[ "$KMS_KEY_ARN" == arn:aws:kms:* ]]; then
  aws kms create-grant \
    --key-id "$KMS_KEY_ARN" \
    --grantee-principal arn:aws:iam::838870929816:role/vmimport \
    --operations Decrypt GenerateDataKey DescribeKey \
    --name "vmimport-s3-${BUCKET}" \
    >/dev/null 2>&1 || true
fi

echo "Starting import-snapshot..."
IMPORT_TASK_ID=$(aws --region "$REGION" ec2 import-snapshot \
  --description "nixos ${HOST} ${DATE}" \
  --disk-container "Format=VHD,UserBucket={S3Bucket=${BUCKET},S3Key=${S3_KEY}}" \
  --query 'ImportTaskId' --output text)

echo "ImportTaskId: $IMPORT_TASK_ID"
