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

	local workspace_name
	workspace_name="${WORKSPACE_NAME:-$(basename "$PWD")}" || true
	workspace_name=$(printf "%s" "$workspace_name" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')
	if [[ -z "$workspace_name" ]]; then
		echo "setup_repo: could not determine workspace name (set WORKSPACE_NAME)" >&2
		return 2
	fi

	# Parse git remote into host + path (supports scp-style SSH, ssh://, https://)
	local host path
	host=""
	path=""
	if [[ "$remote_url" =~ ^git@([^:]+):(.+)$ ]]; then
		host="${match[1]}"
		path="${match[2]}"
	elif [[ "$remote_url" =~ ^ssh://([^/]+)/(.+)$ ]]; then
		host="${match[1]}"
		path="${match[2]}"
	elif [[ "$remote_url" =~ ^https?://([^/]+)/(.+)$ ]]; then
		host="${match[1]}"
		path="${match[2]}"
	else
		echo "setup_repo: unsupported git URL: $remote_url" >&2
		return 2
	fi

	path="${path%/}"
	path="${path#./}"
	path="${path#/}"
	if [[ "$path" == *".."* ]]; then
		echo "setup_repo: refusing suspicious repo path: $path" >&2
		return 2
	fi

	local repo_name
	repo_name=$(basename "$path" .git)
	local bare_dir="$cache_root/$host/${path%.git}.git"
	local worktree_dir="./$repo_name"
	local synthetic_branch="${branch}@${workspace_name}"

	mkdir -p "$(dirname "$bare_dir")"

	if [[ ! -d "$bare_dir" ]]; then
		echo "Creating bare cache: $bare_dir"
		if ! git clone --bare "$remote_url" "$bare_dir"; then
			echo "setup_repo: clone failed" >&2
			return 1
		fi
		git --git-dir="$bare_dir" config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
		git --git-dir="$bare_dir" config --unset-all remote.origin.mirror 2>/dev/null || true
	else
		local current_origin
		current_origin=$(git --git-dir="$bare_dir" remote get-url origin 2>/dev/null || true)
		if [[ -z "$current_origin" ]]; then
			git --git-dir="$bare_dir" remote add origin "$remote_url"
		elif [[ "$current_origin" != "$remote_url" ]]; then
			git --git-dir="$bare_dir" remote set-url origin "$remote_url"
		fi
	fi

	git --git-dir="$bare_dir" fetch origin --prune || return 1

	local start_ref="origin/$branch"
	if ! git --git-dir="$bare_dir" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
		echo "setup_repo: branch '$branch' not found on origin" >&2
		return 1
	fi

	# Make/update the synthetic branch and set it to track origin/<branch>
	git --git-dir="$bare_dir" branch -f "$synthetic_branch" "$start_ref" || return 1
	git --git-dir="$bare_dir" branch --set-upstream-to "$start_ref" "$synthetic_branch" >/dev/null 2>&1 || true

	if [[ -d "$worktree_dir/.git" || -f "$worktree_dir/.git" ]]; then
		echo "Worktree already exists: $worktree_dir"
		git -C "$worktree_dir" config push.default upstream >/dev/null 2>&1 || true
		git -C "$worktree_dir" config opencode.syntheticWorktrees true >/dev/null 2>&1 || true
		return 0
	fi

	echo "Adding worktree '$worktree_dir' on '$synthetic_branch'"
	git --git-dir="$bare_dir" worktree add "$worktree_dir" "$synthetic_branch" || return 1
	git -C "$worktree_dir" config push.default upstream >/dev/null 2>&1 || true
	git -C "$worktree_dir" config opencode.syntheticWorktrees true >/dev/null 2>&1 || true
	git -C "$worktree_dir" config branch."$synthetic_branch".pushRemote origin >/dev/null 2>&1 || true
	git -C "$worktree_dir" config branch."$synthetic_branch".merge "refs/heads/$branch" >/dev/null 2>&1 || true
	git -C "$worktree_dir" config branch."$synthetic_branch".remote origin >/dev/null 2>&1 || true

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
