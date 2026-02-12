# functions
#█▓▒░ 1password
function 1pwaccount() {
	domain="${3:-my}.1password.com"
	op account add \
		--address "$domain" \
		--email "$2" \
		--shorthand "$1"
}
function 1pwsignin() {
	# muliuser fun times
	echo "unlock your keychain 🔐"
	read -rs _pw
	if [[ -n "$_pw" ]]; then
		printf "logging in: "
		accounts=("${(f)$(op account list | tail -n +2 | cut -d' ' -f1)}")
		for acct in "${accounts[@]}" ;do
			printf "%s " "$acct"
			eval $(echo "$_pw" | op signin --account "$acct")
		done
		echo
	fi
}
function 1pwcheck() {
	[[ -z "$(op vault user list private --account $1 2>/dev/null)" ]] && 1pwsignin || return true
}
function 1pw() {
	f="${3:-notesPlain}"
	[[ "$2" =~ "^http" ]] && i=$(1pwurl "$2") || i="$2"
	1pwcheck "$1" && op item get "$i" --account "$1" --fields "$f" --format json | jq -rM '.value'
}
function 1pwedit() {
	[[ -z "$4" ]] && { read val; } || { val=$4; }
	1pwcheck "$1" && op item edit --account "$1" "$2" "${3}=${val}"
}
function 1pwfile() {
	f="${4:-notesPlain}"
	1pwcheck "$1" && op --account "$1" read "op://$2/$3/$f"
}
function 1pweditfile() {
	1pwcheck "$1" && op item edit --account "$1" "$2" "files.[file]=$3"
}
function 1pwurl() {
	echo "$1" | sed 's/^.*i=//;s/\&.*$//'
}

# Helper: check if 1Password is available
function has_op() {
	command -v op >/dev/null 2>&1
}

# Helper: check if at least one account is configured
function has_op_accounts() {
	has_op && [ -n "$(op account list 2>/dev/null)" ]
}

# Helper: check if already unlocked
function is_op_unlocked() {
	has_op_accounts && op account get >/dev/null 2>&1
}

update_secrets() {
	~/.local/bin/update-secrets "$@"
}

function maybe_refresh_secrets() {
	local quiet=0
	[[ "$1" == "--quiet" ]] && quiet=1
	local secrets_target
	secrets_target=$(readlink ~/.secrets 2>/dev/null || echo ~/.secrets)

	if [ ! -e "$secrets_target" ] || find "$secrets_target" -mtime +7 >/dev/null 2>&1; then
		if (( quiet )); then
			update_secrets --quiet
		else
			update_secrets
		fi
	fi

	[ -f ~/.secrets ] && source ~/.secrets
}

# Usage: setup_repo git@git.ap.org:gitops/devcom/terraform-modules/ap-k8s.git [branch]
# Defaults branch to 'main' if not provided.
setup_repo() {
	local remote_url="$1"
	local branch="${2:-main}"
	local cache_root="${CACHE_ROOT:-$HOME/src/cache}"

	if [[ -z "$remote_url" ]]; then
		echo "usage: setup_repo <git-url> [branch]" >&2
		return 2
	fi

	# Extract repo name from URL (last path component without .git)
	local repo_name
	repo_name=$(basename "$remote_url" .git)

	# Determine bare repo path in cache
	local repo_path
	repo_path="$cache_root/$(echo "$remote_url" | sed -E 's#[:/]#/#g; s#\.git$##').git"

	# Create cache clone if it doesn't exist
	if [[ -d "$repo_path" ]]; then
		echo "Cache already exists: $repo_path"
	else
		mkdir -p "$(dirname "$repo_path")"
		echo "Creating bare clone in cache: $repo_path"
		if ! git clone --bare "$remote_url" "$repo_path"; then
			echo "Failed to clone $remote_url" >&2
			return 1
		fi
		# Set proper fetch refspec and remove mirror flag if any
		git --git-dir="$repo_path" config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
		git --git-dir="$repo_path" config --unset-all remote.origin.mirror 2>/dev/null || true
		echo "Bare clone ready: $repo_path"
	fi

	# Ensure the target branch ref exists locally
	if ! git --git-dir="$repo_path" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
		git --git-dir="$repo_path" fetch --prune origin "+refs/heads/$branch:refs/remotes/origin/$branch" >/dev/null 2>&1 || true
	fi

	# Add worktree in current directory
	local worktree_dir="./$repo_name"
	if [[ -d "$worktree_dir/.git" ]]; then
		echo "Worktree already exists: $worktree_dir"
		return 0
	fi

	echo "Adding worktree for branch '$branch' in $worktree_dir"
	if git --git-dir="$repo_path" worktree add "$worktree_dir" "$branch" 2>/dev/null; then
		echo "Worktree ready: $worktree_dir"
		return 0
	fi

	# If the branch doesn't exist locally yet, create it from origin.
	git --git-dir="$repo_path" worktree add -b "$branch" "$worktree_dir" "origin/$branch"
	echo "Worktree ready: $worktree_dir"
}

# Warm gpg-agent cache for signing keys.
#
# Default UIDs are chosen to match the two imported keys in this repo.
# Override with: export GPG_WARMUP_UIDS='"uid1" "uid2"'
#
# Usage:
#   gpg_warmup            # prompts only if stamp older than 24h
#   gpg_warmup --force    # always prompt
gpg_warmup() {
	local force=0
	[[ "${1:-}" == "--force" ]] && force=1

	local ttl_seconds="${GPG_WARMUP_TTL_SECONDS:-86400}"
	local cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}"
	local stamp="$cache_dir/gpg-warmup.stamp"

	mkdir -p "$cache_dir" 2>/dev/null || true

	if (( ! force )) && [[ -f "$stamp" ]]; then
		local now mtime
		now=$(date +%s)
		if stat -c %Y "$stamp" >/dev/null 2>&1; then
			mtime=$(stat -c %Y "$stamp")
		else
			mtime=$(stat -f %m "$stamp" 2>/dev/null || echo 0)
		fi
		if (( now - mtime < ttl_seconds )); then
			echo "gpg_warmup: recently warmed (use --force to reprompt)"
			return 0
		fi
	fi

	if ! command -v gpg >/dev/null 2>&1; then
		echo "gpg_warmup: gpg not found" >&2
		return 127
	fi

	# Make sure pinentry targets the current TTY.
	export GPG_TTY="$(tty 2>/dev/null || true)"
	command -v gpg-connect-agent >/dev/null 2>&1 && gpg-connect-agent updatestartuptty /bye >/dev/null 2>&1 || true

	local uids_str="${GPG_WARMUP_UIDS:-cdenneen@gmail.com cdenneen@ap.org}"
	local -a uids
	uids=(${=uids_str})

	local ok=1
	local uid fpr grip
	for uid in "${uids[@]}"; do
		fpr=$(gpg --list-secret-keys --with-colons --with-keygrip "$uid" 2>/dev/null \
			| sed -n 's/^fpr:::::::::\([0-9A-F]\{40\}\):/\1/p' \
			| sed -n '1p')
		grip=$(gpg --list-secret-keys --with-colons --with-keygrip "$uid" 2>/dev/null \
			| sed -n 's/^grp:::::::::\([0-9A-F]\{40\}\):/\1/p' \
			| sed -n '1p')

		if [[ -z "$fpr" ]]; then
			echo "gpg_warmup: no secret key found for '$uid'" >&2
			ok=0
			continue
		fi

		# Force a prompt even if the passphrase is currently cached.
		if [[ -n "$grip" ]] && command -v gpg-connect-agent >/dev/null 2>&1; then
			gpg-connect-agent "clear_passphrase $grip" /bye >/dev/null 2>&1 || true
		fi

		# Sign a throwaway payload to force passphrase entry / cache fill.
		if ! print -r -- "warmup $(date -Is) $uid" \
			| gpg --local-user "$fpr" --armor --sign --output /dev/null; then
			ok=0
		fi
		done

	if (( ok )); then
		: >| "$stamp"
		return 0
	fi
	return 1
}
