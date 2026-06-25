# Amazon AMI Build + Launch

This repo contains a dedicated NixOS configuration to build an EC2-compatible AMI disk image with a larger EFI System Partition (ESP), plus a small `/etc/nixos/flake.nix` proxy that points back to GitHub.

Targets:

- Region: `us-east-1`
- S3 bucket: `chris-denneen-cloud9`
- Instance profile ARN: `arn:aws:iam::838870929816:instance-profile/ChrisDenneen-Cloud9-Instance-Profile`

Source instance (`eros`) settings to match for networking (queried from IMDS + EC2 API):

- Instance ID: `i-0a3e1df60bde023ad`
- Instance type: `t4g.large`
- Subnet: `subnet-0f00c3c339c1276d3`
- Security group: `sg-09e1e391fa0921f10`
- Key pair: `cdenneen_ed25519_2024`
- Root device: `/dev/xvda` (gp3, 150G)
  Tags:
- Source instance tags (eros):
  - `Name=Chris Denneen nixos-arm64 eros`
  - `servicefamily=infrastructure`

For new resources created for a host named `$HOST` (AMI, snapshot, instance, volumes):

- `Name=Chris Denneen nixos-arm64 $HOST`
- `servicefamily=infrastructure`

The AMI image build config is `nixosConfigurations.amazon-ami`.

## Prereqs

- Nix with flakes enabled.
- AWS CLI v2 authenticated to account `838870929816` and able to:
  - write to `s3://chris-denneen-cloud9/...`
  - run `ec2:ImportSnapshot` (VM Import/Export needs to be set up)
  - run `ec2:RegisterImage`, `ec2:RunInstances`, `ec2:CreateTags`

## 1) Build the AMI disk image (VHD)

From the repo root:

```sh
nix build .#nixosConfigurations.amazon-ami.config.system.build.amazonImage
ls -lah result/
cat result/nix-support/image-info.json
```

You should see a VHD under `result/` (usually `result/amazon-ami.vhd`).

Notes:

- This image is UEFI (`arm64`) and includes an ESP sized to `750M`.
- The image includes `/etc/nixos/flake.nix` as a proxy to `github:cdenneen/home`.
- The image build uses `virtualisation.diskSize = 16384` (MiB). You can override it in `systems/amazon-ami.nix` if needed.

## 2) Upload VHD to S3

```sh
REGION=us-east-1
BUCKET=chris-denneen-cloud9
HOST=nyx
DATE=$(date +%Y%m%d-%H%M%S)
VHD=$(ls -1 result/*.vhd | head -n1)
# Upload under the pre-existing top-level "ami/" prefix in the bucket.
S3_KEY="ami/${HOST}/${DATE}/$(basename "$VHD")"

aws --region "$REGION" s3 cp "$VHD" "s3://${BUCKET}/${S3_KEY}"
```

## 3) Import snapshot from S3 (VM Import/Export)

Start the import:

```sh
REGION=us-east-1
BUCKET=chris-denneen-cloud9
HOST=nyx

IMPORT_TASK_ID=$(aws --region "$REGION" ec2 import-snapshot \
  --description "nixos ${HOST} ${DATE}" \
  --disk-container "Format=VHD,UserBucket={S3Bucket=${BUCKET},S3Key=${S3_KEY}}" \
  --query 'ImportTaskId' --output text)

echo "$IMPORT_TASK_ID"
```

Wait until the import is complete:

```sh
aws --region "$REGION" ec2 describe-import-snapshot-tasks \
  --import-task-ids "$IMPORT_TASK_ID" \
  --query 'ImportSnapshotTasks[0].SnapshotTaskDetail.{Status:Status,Progress:Progress,SnapshotId:SnapshotId,StatusMessage:StatusMessage}' \
  --output table
```

When `Status` is `completed`, capture the snapshot id:

```sh
SNAPSHOT_ID=$(aws --region "$REGION" ec2 describe-import-snapshot-tasks \
  --import-task-ids "$IMPORT_TASK_ID" \
  --query 'ImportSnapshotTasks[0].SnapshotTaskDetail.SnapshotId' \
  --output text)

echo "$SNAPSHOT_ID"
```

Tag the snapshot for compliance/CUR:

```sh
aws --region "$REGION" ec2 create-tags \
  --resources "$SNAPSHOT_ID" \
  --tags Key=Name,Value="Chris Denneen nixos-arm64 ${HOST}" Key=servicefamily,Value=infrastructure
```

## 4) Register the AMI (UEFI, arm64)

Register the AMI from the snapshot, using the same tags:

```sh
REGION=us-east-1

AMI_NAME="nixos-${HOST}-${DATE}"
AMI_ID=$(aws --region "$REGION" ec2 register-image \
  --name "$AMI_NAME" \
  --architecture arm64 \
  --virtualization-type hvm \
  --boot-mode uefi \
  --ena-support \
  --root-device-name /dev/xvda \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={SnapshotId=${SNAPSHOT_ID},VolumeSize=150,VolumeType=gp3,DeleteOnTermination=true}" \
  --tag-specifications \
    "ResourceType=image,Tags=[{Key=Name,Value=Chris Denneen nixos-arm64 ${HOST}},{Key=servicefamily,Value=infrastructure}]" \
    "ResourceType=snapshot,Tags=[{Key=Name,Value=Chris Denneen nixos-arm64 ${HOST}},{Key=servicefamily,Value=infrastructure}]" \
  --query 'ImageId' --output text)

echo "$AMI_ID"
```

Wait until the AMI is available:

```sh
aws --region "$REGION" ec2 describe-images --image-ids "$AMI_ID" \
  --query 'Images[0].State' --output text
```

## 5) Launch a new instance from the AMI (match eros networking)

Launch with the same subnet + SG as the current `eros`:

```sh
REGION=us-east-1
SUBNET_ID=subnet-0f00c3c339c1276d3
SG_ID=sg-09e1e391fa0921f10
KEY_NAME=cdenneen_ed25519_2024
PROFILE_ARN=arn:aws:iam::838870929816:instance-profile/ChrisDenneen-Cloud9-Instance-Profile
HOST=nyx

NEW_INSTANCE_ID=$(aws --region "$REGION" ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type t4g.large \
  --subnet-id "$SUBNET_ID" \
  --security-group-ids "$SG_ID" \
  --key-name "$KEY_NAME" \
  --iam-instance-profile Arn="$PROFILE_ARN" \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=150,VolumeType=gp3,DeleteOnTermination=true}" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=Chris Denneen nixos-arm64 ${HOST}},{Key=servicefamily,Value=infrastructure}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=Chris Denneen nixos-arm64 ${HOST}},{Key=servicefamily,Value=infrastructure}]" \
  --query 'Instances[0].InstanceId' --output text)

echo "$NEW_INSTANCE_ID"
```

Wait for running:

```sh
aws --region "$REGION" ec2 wait instance-running --instance-ids "$NEW_INSTANCE_ID"
aws --region "$REGION" ec2 describe-instances --instance-ids "$NEW_INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].{PrivateIp:PrivateIpAddress,PublicIp:PublicIpAddress,State:State.Name}' \
  --output table
```

## 6) First boot: rebuild from the GitHub proxy flake

The image bakes a proxy flake at `/etc/nixos/flake.nix` pointing to `github:cdenneen/home`.

On the new instance:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#${HOST}
```

`${HOST}` sets `networking.hostName` accordingly.

## Sops key bootstrap

This repo uses `sops-nix` + AGE. The private key must exist on the instance at:

- `/var/sops/age/keys.txt`

The host configuration includes a first-boot service that generates a host AGE key if missing and prints the **public** key to the journal.

After the instance is up:

1. Get the public key from the new instance:

```sh
journalctl -u sops-age-keygen --no-pager
```

2. Add it to the repo recipients:

- Add to `pub/age-recipients.txt`
- Add to `.sops.yaml` (as `&server_${HOST} ...` and include it in `creation_rules`)

3. Re-encrypt secrets with the new recipient:

```sh
sops-update-keys
```

4. Switch again so it can decrypt immediately:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#${HOST}
```

## Cleanup (optional)

- Delete the S3 object: `aws s3 rm s3://chris-denneen-cloud9/${S3_KEY}`
- Deregister AMI: `aws ec2 deregister-image --image-id $AMI_ID`
- Delete snapshot: `aws ec2 delete-snapshot --snapshot-id $SNAPSHOT_ID`

## Onboarding a new EC2 host (example: nova)

This repo separates:

- `nixosConfigurations.amazon-ami` (generic AMI image builder)
- per-host EC2 configs (e.g. `nixosConfigurations.nyx`)

To create a new host named `nova`:

1. Add a new system module

- Create `systems/nova.nix` by copying `systems/nyx.nix`.
- Change `networking.hostName` to `"nova"`.
- Ensure the host includes the generic EC2 base module (`systems/ec2-base.nix`) so it generates `/var/sops/age/keys.txt` on first boot.

2. Register the new system in the flake

- In `systems/default.nix`, add:
  - `nova = nixosSystem { system = "aarch64-linux"; nixosModules = [ ./ec2-base.nix ./nova.nix "${nixpkgs-unstable}/nixos/modules/virtualisation/amazon-image.nix" ]; };`

3. Build + import + register the AMI

- Use `nixosConfigurations.amazon-ami` to build the VHD and follow the same S3 upload/import/register steps in this document.
- Use a `nova`-specific naming convention in the AWS CLI variables:
  - `S3_KEY="ami/nova/${DATE}/..."`
  - `--description "nixos nova ${DATE}"`
  - `AMI_NAME="nixos-nova-${DATE}"`

4. Launch an instance for nova

- When running `aws ec2 run-instances`, tag the instance/volume as `nova`:
  - `Name=Chris Denneen nixos-arm64 nova`
  - `servicefamily=infrastructure`

5. First boot: switch to the nova configuration

- On the new instance:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#nova
```

6. Enable sops decryption for nova

- On nova, get the AGE public key:

```sh
journalctl -u sops-age-keygen --no-pager
```

- Add the public key to:
  - `pub/age-recipients.txt`
  - `.sops.yaml` (add `&server_nova ...` and include it in `creation_rules`)

- Re-encrypt:

```sh
sops-update-keys
```

- Switch again on nova:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#nova
```
