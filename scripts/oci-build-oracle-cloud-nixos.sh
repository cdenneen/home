#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: oci-build-oracle-cloud-nixos.sh [options]

Builds/installs the existing flake host `ghost` on OCI Always Free A1.

Options:
  --name <display-name>          Instance display name (default: ghost)
  --profile <oci-profile>        OCI CLI profile (default: DEFAULT)
  --compartment-id <ocid>        Compartment OCID (default: tenancy OCID from OCI config)
  --regions <csv>                Comma-separated regions to hunt (default: subscribed READY regions)
  --ssh-pub <path>               SSH public key file (default: ~/.ssh/id_ed25519.pub)
  --ssh-key <path>               SSH private key for nixos-anywhere (default: ~/.ssh/id_ed25519)
  --flake <flake-ref>            nixos-anywhere flake ref (default: .#ghost)
  --boot-gb <size>               Boot volume size in GB (default: 200)
  --max-a1-instances <count>     Exit if this many A1 instances already exist (default: 1)
  --retry-hours <hours>          Retry on capacity/timeouts for N hours (default: 0)
  --retry-forever                Retry on capacity/timeouts until the host is ready
  --retry-sleep-sec <seconds>    Sleep between retry attempts (default: 300)
  --launch-timeout-sec <seconds> Max seconds per single launch API call (default: 180)
  --lock-file <path>             Lock path for cron/parallel safety
                                 (default: ~/.local/state/oci-ghost-build.lock)
  --no-shuffle-ads               Keep AD order as returned by OCI
  --no-install                   Only launch instance; skip nixos-anywhere install
  --help                         Show this help

Notes:
  - Initial provisioning is pinned to a small bootstrap shape: 1/4.
  - After the instance reaches RUNNING, we attempt a non-fatal resize to 2/24.
  - Fault domain is intentionally NOT specified (OCI can pick best).
EOF
}

name="ghost"
profile="DEFAULT"
compartment_id=""
regions_csv=""
ssh_pub_file="$HOME/.ssh/id_ed25519.pub"
ssh_key_file="$HOME/.ssh/id_ed25519"
flake_ref=".#ghost"
boot_gb="200"
max_a1_instances="1"
retry_hours="0"
retry_forever="0"
retry_sleep_sec="300"
launch_timeout_sec="180"
lock_file="${XDG_STATE_HOME:-$HOME/.local/state}/oci-ghost-build.lock"
shuffle_ads="1"
do_install="1"
bootstrap_ocpus="1"
bootstrap_mem_gb="4"
target_ocpus="2"
target_mem_gb="24"

while [ $# -gt 0 ]; do
  case "$1" in
    --name)
      name="$2"; shift 2 ;;
    --profile)
      profile="$2"; shift 2 ;;
    --compartment-id)
      compartment_id="$2"; shift 2 ;;
    --regions)
      regions_csv="$2"; shift 2 ;;
    --ssh-pub)
      ssh_pub_file="$2"; shift 2 ;;
    --ssh-key)
      ssh_key_file="$2"; shift 2 ;;
    --flake)
      flake_ref="$2"; shift 2 ;;
    --boot-gb)
      boot_gb="$2"; shift 2 ;;
    --max-a1-instances)
      max_a1_instances="$2"; shift 2 ;;
    --retry-hours)
      retry_hours="$2"; shift 2 ;;
    --retry-forever)
      retry_forever="1"; shift ;;
    --retry-sleep-sec)
      retry_sleep_sec="$2"; shift 2 ;;
    --launch-timeout-sec)
      launch_timeout_sec="$2"; shift 2 ;;
    --lock-file)
      lock_file="$2"; shift 2 ;;
    --no-shuffle-ads)
      shuffle_ads="0"; shift ;;
    --no-install)
      do_install="0"; shift ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

for cmd in oci jq nix ssh awk sed; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if ! [[ "$retry_hours" =~ ^[0-9]+$ ]]; then
  echo "--retry-hours must be an integer >= 0" >&2
  exit 1
fi

if ! [[ "$retry_sleep_sec" =~ ^[0-9]+$ ]] || [ "$retry_sleep_sec" -lt 1 ]; then
  echo "--retry-sleep-sec must be an integer >= 1" >&2
  exit 1
fi

if ! [[ "$launch_timeout_sec" =~ ^[0-9]+$ ]] || [ "$launch_timeout_sec" -lt 30 ]; then
  echo "--launch-timeout-sec must be an integer >= 30" >&2
  exit 1
fi

if ! [[ "$max_a1_instances" =~ ^[0-9]+$ ]] || [ "$max_a1_instances" -lt 1 ]; then
  echo "--max-a1-instances must be an integer >= 1" >&2
  exit 1
fi

if [ ! -r "$ssh_pub_file" ]; then
  echo "SSH pub key not readable: $ssh_pub_file" >&2
  exit 1
fi

if [ "$do_install" = "1" ] && [ ! -r "$ssh_key_file" ]; then
  echo "SSH private key not readable: $ssh_key_file" >&2
  exit 1
fi

if [ -z "$lock_file" ]; then
  echo "Internal error: lock_file resolved to an empty path" >&2
  exit 1
fi

lock_parent="$lock_file"
case "$lock_parent" in
  */*)
    lock_parent="${lock_parent%/*}"
    ;;
  *)
    lock_parent="."
    ;;
esac

[ -n "$lock_parent" ] || lock_parent="."

mkdir -p "$lock_parent"
lock_dir="${lock_file}.d"
lock_pid_file="$lock_dir/pid"

pid_matches_builder() {
  local pid="$1"
  local cmd=""

  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  [ -n "$cmd" ] && printf '%s' "$cmd" | grep -Fq "oci-build-oracle-cloud-nixos.sh"
}

acquire_lock() {
  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$$" > "$lock_pid_file"
    return 0
  fi

  existing_pid=""
  if [ -r "$lock_pid_file" ]; then
    existing_pid="$(cat "$lock_pid_file" 2>/dev/null || true)"
  fi

  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null && pid_matches_builder "$existing_pid"; then
    echo "Another launch run appears active (pid: $existing_pid, lock: $lock_dir); exiting."
    return 1
  fi

  echo "Found stale lock at $lock_dir; removing and continuing."
  rm -rf "$lock_dir"

  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$$" > "$lock_pid_file"
    return 0
  fi

  echo "Could not acquire lock (lock: $lock_dir); exiting."
  return 1
}

shape_below_target() {
  local current_ocpus="$1"
  local current_mem_gb="$2"

  awk -v c="$current_ocpus" -v m="$current_mem_gb" -v tc="$target_ocpus" -v tm="$target_mem_gb" \
    'BEGIN { exit (c+0 < tc+0 || m+0 < tm+0) ? 0 : 1 }'
}

retry_window_open() {
  if [ "$retry_forever" = "1" ]; then
    return 0
  fi

  [ "$retry_hours" -gt 0 ] && [ "$(date +%s)" -lt "$deadline_epoch" ]
}

retry_wait_message() {
  if [ "$retry_forever" = "1" ]; then
    printf 'retrying in %ss' "$retry_sleep_sec"
    return 0
  fi

  local remaining
  remaining=$((deadline_epoch - $(date +%s)))
  [ "$remaining" -ge 0 ] || remaining=0
  printf 'retrying in %ss (remaining %ss)' "$retry_sleep_sec" "$remaining"
}

refresh_instance_shape() {
  instance_json=$(oci --profile "$profile" --region "$instance_region" compute instance get \
    --instance-id "$instance_id" --output json)
  current_shape=$(printf '%s' "$instance_json" | jq -r '.data.shape // empty')
  current_ocpus=$(printf '%s' "$instance_json" | jq -r '.data."shape-config".ocpus // .data."shapeConfig".ocpus // 0')
  current_mem_gb=$(printf '%s' "$instance_json" | jq -r '.data."shape-config"."memory-in-gbs" // .data."shape-config"."memoryInGBs" // .data."shapeConfig"."memoryInGBs" // 0')
}

maybe_resize_to_target() {
  local current_shape="$1"
  local current_ocpus="$2"
  local current_mem_gb="$3"
  local out rc message code

  if [ "$current_shape" != "VM.Standard.A1.Flex" ]; then
    echo "Skipping resize: current shape is ${current_shape:-unknown}"
    return 0
  fi

  if ! shape_below_target "$current_ocpus" "$current_mem_gb"; then
    echo "Instance already meets or exceeds target free-tier size: ${current_ocpus}/${current_mem_gb}"
    return 0
  fi

  echo "Attempting post-provision resize to ${target_ocpus} OCPU / ${target_mem_gb} GB..."
  set +e
  out=$(
    oci --profile "$profile" --region "$instance_region" compute instance update \
      --instance-id "$instance_id" \
      --shape "VM.Standard.A1.Flex" \
      --shape-config "{\"ocpus\":$target_ocpus,\"memoryInGBs\":$target_mem_gb}" \
      --update-operation-constraint ALLOW_DOWNTIME \
      --wait-for-state RUNNING \
      --max-wait-seconds 1200 \
      --wait-interval-seconds 20 \
      --output json 2>&1
  )
  rc=$?
  set -e

  if [ $rc -eq 0 ]; then
    echo "Resize succeeded; instance returned to RUNNING at ${target_ocpus}/${target_mem_gb}."
    echo "GHOST_RESIZE_OK name=$name instance_id=$instance_id region=$instance_region ocpus=$target_ocpus memory_gb=$target_mem_gb"
    return 0
  fi

  message=$(printf '%s' "$out" | jq -r '.message // empty' 2>/dev/null || true)
  code=$(printf '%s' "$out" | jq -r '.code // empty' 2>/dev/null || true)
  if [ -z "$message" ]; then
    message=$(printf '%s' "$out" | sed -n '1,40p' | tr '\n' ' ')
  fi

  echo "Warning: resize to ${target_ocpus}/${target_mem_gb} failed code=${code:-unknown}: $message" >&2
  echo "Continuing on the bootstrap shape so provisioning can still finish." >&2
  return 0
}

if ! acquire_lock; then
  exit 0
fi

cleanup_lock() {
  rm -f "$lock_pid_file" 2>/dev/null || true
  rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup_lock EXIT INT TERM

oci_cfg_file="${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}"
if [ ! -r "$oci_cfg_file" ]; then
  echo "OCI config not readable: $oci_cfg_file" >&2
  exit 1
fi

oci_cfg_get() {
  local key="$1"
  awk -v prof="$profile" -v key="$key" '
    BEGIN { current = "" }
    /^[[:space:]]*#/ { next }
    /^\[/ {
      line = $0
      gsub(/^\[/, "", line)
      gsub(/\]$/, "", line)
      current = line
      next
    }
    current == prof {
      if ($0 ~ "^[[:space:]]*" key "[[:space:]]*=") {
        split($0, parts, "=")
        value = parts[2]
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
        print value
        exit
      }
    }
  ' "$oci_cfg_file"
}

tenancy_id="$(oci_cfg_get tenancy || true)"
profile_region="$(oci_cfg_get region || true)"

if [ -z "$tenancy_id" ]; then
  echo "Could not read tenancy from profile [$profile] in $oci_cfg_file" >&2
  exit 1
fi

if [ -z "$compartment_id" ]; then
  compartment_id="$tenancy_id"
fi

declare -a regions
if [ -n "$regions_csv" ]; then
  IFS=',' read -r -a raw_regions <<< "$regions_csv"
  for r in "${raw_regions[@]}"; do
    trimmed="$(printf '%s' "$r" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -n "$trimmed" ] && regions+=("$trimmed")
  done
else
  mapfile -t regions < <(
    oci --profile "$profile" iam region-subscription list --output json \
      | jq -r '.data[] | select(.status=="READY") | ."region-name"'
  )
fi

if [ ${#regions[@]} -eq 0 ]; then
  if [ -n "$profile_region" ]; then
    regions=("$profile_region")
  else
    echo "No OCI regions available. Provide --regions or set region in profile [$profile]." >&2
    exit 1
  fi
fi

echo "Target regions: ${regions[*]}"

ssh_pub_key="$(cat "$ssh_pub_file")"

launch_timeout_cmd=""
if command -v timeout >/dev/null 2>&1; then
  launch_timeout_cmd="$(command -v timeout)"
elif command -v gtimeout >/dev/null 2>&1; then
  launch_timeout_cmd="$(command -v gtimeout)"
fi

total_a1_instances=0
for region in "${regions[@]}"; do
  count_in_region="$((
    $(oci --profile "$profile" --region "$region" compute instance list \
      --compartment-id "$compartment_id" --all --output json \
      | jq '[.data[] | select(.shape=="VM.Standard.A1.Flex" and ."lifecycle-state"!="TERMINATED")] | length')
  ))"
  total_a1_instances=$((total_a1_instances + count_in_region))
done

if [ "$total_a1_instances" -ge "$max_a1_instances" ]; then
  echo "A1 instance count is $total_a1_instances (>= $max_a1_instances); nothing to launch."
  exit 0
fi

instance_id=""
instance_region=""
for region in "${regions[@]}"; do
  existing_instance_id=$(oci --profile "$profile" --region "$region" compute instance list \
    --compartment-id "$compartment_id" --all --output json \
    | jq -r --arg n "$name" '[.data[] | select(."display-name"==$n and ."lifecycle-state"!="TERMINATED") | .id][0] // empty')
  if [ -n "$existing_instance_id" ]; then
    instance_id="$existing_instance_id"
    instance_region="$region"
    echo "Reusing existing instance: $instance_id (region: $instance_region)"
    break
  fi
done

if [ -z "$instance_id" ]; then
  deadline_epoch=0
  if [ "$retry_forever" = "1" ]; then
    echo "Retry mode enabled forever; sleep=${retry_sleep_sec}s"
  elif [ "$retry_hours" -gt 0 ]; then
    deadline_epoch=$(( $(date +%s) + (retry_hours * 3600) ))
    echo "Retry mode enabled for ${retry_hours}h; sleep=${retry_sleep_sec}s"
  fi

  attempt=1
  while :; do
    instance_id=""
    instance_region=""
    last_capacity_error=""
    saw_non_capacity_error="0"
    saw_quota_block="0"
    saw_retryable_limit="0"
    saw_retryable_transport="0"
    first_non_capacity_error=""

    echo "Launch attempt #$attempt"

    for shape in "${bootstrap_ocpus}:${bootstrap_mem_gb}"; do
      ocpus="${shape%%:*}"
      mem_gb="${shape##*:}"
      echo "Trying A1 shape ${ocpus} OCPU / ${mem_gb} GB"

      for region in "${regions[@]}"; do
        core_available=$(oci --profile "$profile" --region "$region" limits resource-availability get \
          --service-name compute --limit-name standard-a1-core-regional-count --compartment-id "$compartment_id" --output json \
          | jq -r '.data.available // 0' 2>/dev/null || echo 0)
        mem_available=$(oci --profile "$profile" --region "$region" limits resource-availability get \
          --service-name compute --limit-name standard-a1-memory-regional-count --compartment-id "$compartment_id" --output json \
          | jq -r '.data.available // 0' 2>/dev/null || echo 0)

        if ! awk -v c="$core_available" -v m="$mem_available" -v oc="$ocpus" -v mg="$mem_gb" 'BEGIN{exit (c+0 >= oc+0 && m+0 >= mg+0) ? 0 : 1}'; then
          echo "  Region $region skipped: regional A1 limit availability core=${core_available} mem=${mem_available} (needs ${ocpus}/${mem_gb})"
          saw_quota_block="1"
          continue
        fi

        subnet_id=$(oci --profile "$profile" --region "$region" network subnet list \
          --compartment-id "$compartment_id" --all --output json \
          | jq -r '[.data[] | select(."lifecycle-state"=="AVAILABLE" and ."prohibit-public-ip-on-vnic"==false) | .id][0] // empty')
        if [ -z "$subnet_id" ]; then
          echo "  Region $region skipped: no AVAILABLE subnet with public IPs allowed"
          continue
        fi

        image_id=$(oci --profile "$profile" --region "$region" compute image list \
          --compartment-id "$compartment_id" --all --sort-by TIMECREATED --sort-order DESC --output json \
          | jq -r '[.data[] | select(."display-name"|test("^Canonical-Ubuntu-24\\.04-Minimal-aarch64")) | .id][0] // empty')
        if [ -z "$image_id" ]; then
          echo "  Region $region skipped: no Ubuntu 24.04 Minimal ARM image found"
          continue
        fi

        mapfile -t ads < <(
          oci --profile "$profile" --region "$region" iam availability-domain list \
            --compartment-id "$tenancy_id" --output json | jq -r '.data[].name'
        )
        if [ ${#ads[@]} -eq 0 ]; then
          echo "  Region $region skipped: no availability domains discovered"
          continue
        fi

        if [ "$shuffle_ads" = "1" ] && [ ${#ads[@]} -gt 1 ]; then
          mapfile -t ads < <(printf '%s\n' "${ads[@]}" | awk 'BEGIN{srand()} {print rand() "\t" $0}' | sort -k1,1n | cut -f2-)
        fi

        echo "  Region: $region"
        for ad in "${ads[@]}"; do
          echo "    AD: $ad"
          set +e
          if [ -n "$launch_timeout_cmd" ]; then
            out=$(
              "$launch_timeout_cmd" "$launch_timeout_sec" \
                oci --profile "$profile" --region "$region" compute instance launch \
                  --connection-timeout 20 \
                  --read-timeout 120 \
                  --max-retries 0 \
                  --availability-domain "$ad" \
                  --compartment-id "$compartment_id" \
                  --display-name "$name" \
                  --shape "VM.Standard.A1.Flex" \
                  --shape-config "{\"ocpus\":$ocpus,\"memoryInGBs\":$mem_gb}" \
                  --subnet-id "$subnet_id" \
                  --assign-public-ip true \
                  --image-id "$image_id" \
                  --boot-volume-size-in-gbs "$boot_gb" \
                  --metadata "{\"ssh_authorized_keys\":\"$ssh_pub_key\"}" \
                  --output json 2>&1
            )
          else
            out=$(oci --profile "$profile" --region "$region" compute instance launch \
              --connection-timeout 20 \
              --read-timeout 120 \
              --max-retries 0 \
              --availability-domain "$ad" \
              --compartment-id "$compartment_id" \
              --display-name "$name" \
              --shape "VM.Standard.A1.Flex" \
              --shape-config "{\"ocpus\":$ocpus,\"memoryInGBs\":$mem_gb}" \
              --subnet-id "$subnet_id" \
              --assign-public-ip true \
              --image-id "$image_id" \
              --boot-volume-size-in-gbs "$boot_gb" \
              --metadata "{\"ssh_authorized_keys\":\"$ssh_pub_key\"}" \
              --output json 2>&1)
          fi
          rc=$?
          set -e

          if [ $rc -eq 0 ]; then
            instance_id=$(printf '%s' "$out" | jq -r '.data.id')
            instance_region="$region"
            echo "Launched instance: $instance_id (region: $instance_region)"
            echo "GHOST_LAUNCHED name=$name instance_id=$instance_id region=$instance_region"
            break 3
          fi

          if [ $rc -eq 124 ] || [ $rc -eq 137 ] || [ $rc -eq 143 ]; then
            saw_retryable_transport="1"
            echo "      launch timed out after ${launch_timeout_sec}s; treating as transient"
            continue
          fi

          message=$(printf '%s' "$out" | jq -r '.message // empty' 2>/dev/null || true)
          code=$(printf '%s' "$out" | jq -r '.code // empty' 2>/dev/null || true)
          if [ -z "$message" ]; then
            message=$(printf '%s' "$out" | sed -n '1,40p' | tr '\n' ' ')
          fi
          echo "      launch failed code=${code:-unknown}: $message"

          if printf '%s' "$message" | grep -qi 'Out of host capacity'; then
            last_capacity_error="$message"
            continue
          fi

          if printf '%s' "$message" | grep -qi 'LimitExceeded' && printf '%s' "$message" | grep -qi 'standard-a1'; then
            last_capacity_error="$message"
            saw_retryable_limit="1"
            continue
          fi

          if printf '%s' "$message" | grep -Eqi 'TooManyRequests|rate limit'; then
            echo "      hit API throttling; pausing 30s"
            sleep 30
            continue
          fi

          if printf '%s' "$message" | grep -Eqi 'connection to endpoint timed out|read timed out|connect timeout|temporarily unavailable|unable to connect|connection aborted'; then
            saw_retryable_transport="1"
            echo "      transient OCI/API timeout; pausing 20s"
            sleep 20
            continue
          fi

          saw_non_capacity_error="1"
          if [ -z "$first_non_capacity_error" ]; then
            first_non_capacity_error="$message"
          fi
        done
      done
    done

    if [ -n "$instance_id" ]; then
      break
    fi

    if [ "$saw_non_capacity_error" = "1" ]; then
      echo "Launch failed due to non-capacity error: $first_non_capacity_error" >&2
      exit 2
    fi

    if [ "$saw_quota_block" = "1" ] || [ "$saw_retryable_limit" = "1" ] || [ "$saw_retryable_transport" = "1" ]; then
      if retry_window_open; then
        echo "A1 quota/capacity/transient errors; $(retry_wait_message)"
        sleep "$retry_sleep_sec"
        attempt=$((attempt + 1))
        continue
      fi
      echo "Launch blocked: no usable A1 free-tier quota/capacity in selected regions." >&2
      echo "Hint: free A1 cores appear fully allocated; release old A1 instances or wait for quota/capacity to free." >&2
      exit 2
    fi

    if retry_window_open; then
      echo "Capacity unavailable; $(retry_wait_message)"
      sleep "$retry_sleep_sec"
      attempt=$((attempt + 1))
      continue
    fi

    if [ -n "$last_capacity_error" ]; then
      echo "All launch attempts failed: $last_capacity_error" >&2
    else
      echo "All launch attempts failed." >&2
    fi
    exit 2
  done
fi

echo "Waiting for instance to be RUNNING..."
running="0"
for _ in $(seq 1 60); do
  state=$(oci --profile "$profile" --region "$instance_region" compute instance get --instance-id "$instance_id" --output json | jq -r '.data."lifecycle-state"')
  if [ "$state" = "RUNNING" ]; then
    running="1"
    break
  fi
  sleep 10
done
if [ "$running" != "1" ]; then
  echo "Instance did not reach RUNNING in time." >&2
  exit 3
fi

refresh_instance_shape
maybe_resize_to_target "$current_shape" "$current_ocpus" "$current_mem_gb"
refresh_instance_shape

vnic_id=$(oci --profile "$profile" --region "$instance_region" compute vnic-attachment list \
  --compartment-id "$compartment_id" --instance-id "$instance_id" --output json \
  | jq -r '.data[0]."vnic-id"')

public_ip=$(oci --profile "$profile" --region "$instance_region" network vnic get --vnic-id "$vnic_id" --output json \
  | jq -r '.data."public-ip"')

if [ -z "$public_ip" ] || [ "$public_ip" = "null" ]; then
  echo "Instance has no public IP; cannot continue with nixos-anywhere." >&2
  exit 3
fi

echo "Instance public IP: $public_ip (region: $instance_region)"

if [ "$do_install" = "0" ]; then
  echo "Launch-only mode; skipping nixos-anywhere install."
  exit 0
fi

echo "Waiting for SSH on ubuntu@$public_ip ..."
ssh_ready="0"
for _ in $(seq 1 60); do
  if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/dev/null -i "$ssh_key_file" "ubuntu@$public_ip" true 2>/dev/null; then
    ssh_ready="1"
    break
  fi
  sleep 5
done
if [ "$ssh_ready" != "1" ]; then
  echo "SSH did not become ready in time on ubuntu@$public_ip" >&2
  exit 4
fi

echo "Running nixos-anywhere for flake $flake_ref"
nix run github:nix-community/nixos-anywhere -- \
  --flake "$flake_ref" \
  --target-host "ubuntu@$public_ip" \
  --ssh-option StrictHostKeyChecking=accept-new \
  --ssh-option UserKnownHostsFile=/dev/null \
  --build-on remote \
  -i "$ssh_key_file"

refresh_instance_shape
echo "NixOS install completed for $name ($instance_id) at $public_ip in $instance_region"
echo "GHOST_READY name=$name instance_id=$instance_id public_ip=$public_ip region=$instance_region shape=$current_shape ocpus=$current_ocpus memory_gb=$current_mem_gb"
