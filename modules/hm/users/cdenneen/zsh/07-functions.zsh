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

_setup_repo_workspace_name() {
	setopt localoptions extendedglob
	local ws
	ws="${WORKSPACE_NAME:-${PWD:t}}"
	# Replace non-allowed chars with '-'
	ws="${ws//[^A-Za-z0-9._-]/-}"
	# Trim leading/trailing '-'
	ws="${ws##[-]#}"
	ws="${ws%%[-]#}"
	printf "%s" "$ws"
}

_setup_repo_parse_remote() {
	# prints: <host>\n<path>\n ; returns non-zero on failure
	local remote_url="$1"
	local host remote_path
	host=""
	remote_path=""
	if [[ "$remote_url" =~ ^git@([^:]+):(.+)$ ]]; then
		host="${match[1]}"
		remote_path="${match[2]}"
	elif [[ "$remote_url" =~ ^ssh://([^/]+)/(.+)$ ]]; then
		host="${match[1]}"
		remote_path="${match[2]}"
	elif [[ "$remote_url" =~ ^https?://([^/]+)/(.+)$ ]]; then
		host="${match[1]}"
		remote_path="${match[2]}"
	else
		return 1
	fi

	remote_path="${remote_path%/}"
	remote_path="${remote_path#./}"
	remote_path="${remote_path#/}"
	if [[ "$remote_path" == *".."* ]]; then
		return 1
	fi

	print -r -- "$host"
	print -r -- "$remote_path"
}

_setup_repo_expected_bare_dir() {
	# Returns the expected bare cache dir for a remote.
	# Layout: flat key under CACHE_ROOT.
	# Example: /home/user/src/cache/github.com_org_repo.git
	setopt localoptions extendedglob
	local cache_root="$1"
	local host="$2"
	local remote_path="$3"

	local base key
	base="$host/${remote_path%.git}"
	key="$base"
	key="${key//\//_}"
	key="${key//:/_}"
	key="${key//@/_}"
	printf "%s/%s.git" "$cache_root" "$key"
}

_setup_repo_origin_default_branch() {
	# Determine origin's default branch from refs/remotes/origin/HEAD.
	# Prints branch name, defaults to "main" if unknown.
	local bare_dir="$1"
	local ref branch
	# Prefer remote HEAD if present.
	ref=$(git --git-dir="$bare_dir" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)
	branch="${ref##*/}"
	if [[ -n "$branch" ]]; then
		print -r -- "$branch"
		return 0
	fi
	# Fall back to bare HEAD (common for freshly cloned bare repos).
	ref=$(git --git-dir="$bare_dir" symbolic-ref -q HEAD 2>/dev/null || true)
	branch="${ref##*/}"
	if [[ -n "$branch" ]]; then
		print -r -- "$branch"
		return 0
	fi
	if git --git-dir="$bare_dir" show-ref --verify --quiet refs/remotes/origin/main; then
		print -r -- main
		return 0
	fi
	if git --git-dir="$bare_dir" show-ref --verify --quiet refs/remotes/origin/master; then
		print -r -- master
		return 0
	fi
	print -r -- main
}

_setup_repo_worktree_default_branch() {
	# Determine default branch for an existing worktree.
	# Prefer origin/HEAD if present, fall back to local HEAD branch.
	local wt_dir="$1"
	local ref branch
	ref=$(git -C "$wt_dir" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)
	branch="${ref##*/}"
	if [[ -n "$branch" ]]; then
		print -r -- "$branch"
		return 0
	fi
	ref=$(git -C "$wt_dir" symbolic-ref -q HEAD 2>/dev/null || true)
	branch="${ref##*/}"
	if [[ -n "$branch" ]]; then
		print -r -- "$branch"
		return 0
	fi
	print -r -- main
}

_git_common_dir_abs() {
	# Prints absolute git common dir for a worktree.
	# Avoids newer git-only flags like --path-format.
	local wt_dir="$1"
	local common
	common=$(git -C "$wt_dir" rev-parse --git-common-dir 2>/dev/null || true)
	if [[ -z "$common" ]]; then
		return 1
	fi
	if [[ "$common" == /* ]]; then
		print -r -- "$common"
		return 0
	fi
	(
		cd "$wt_dir" 2>/dev/null || exit 1
		cd "$common" 2>/dev/null || exit 1
		pwd -P
	)
}

_worktree_common_dir_from_gitfile() {
	# Best-effort common dir derivation from worktree .git file.
	# Prints the bare repo dir (common dir) if it looks like a linked worktree.
	setopt localoptions extendedglob
	local wt_dir="$1"
	local raw first gitdir
	raw="$(<"$wt_dir/.git" 2>/dev/null)"
	[[ -n "$raw" ]] || return 1
	# First line only.
	first="${${(f)raw}[1]}"
	# Trim leading whitespace.
	first="${first##[[:space:]]#}"
	[[ "$first" == gitdir:* ]] || return 1
	gitdir="${first#gitdir:}"
	gitdir="${gitdir##[[:space:]]#}"
	gitdir="${gitdir%$'\r'}"
	[[ -n "$gitdir" ]] || return 1

	# Resolve relative gitdir if needed.
	if [[ "$gitdir" != /* ]]; then
		(
			cd "$wt_dir" 2>/dev/null || exit 1
			cd "$gitdir" 2>/dev/null || exit 1
			gitdir="$(pwd -P)" || exit 1
			print -r -- "$gitdir"
		) | {
			read -r gitdir
			:
		}
	fi

	if [[ "$gitdir" == */worktrees/* ]]; then
		print -r -- "${gitdir%/worktrees/*}"
		return 0
	fi

	return 1
}

_setup_repo_expected_bare_dir() {
	# Returns the expected bare cache dir for a remote.
	# Layout: flat key under CACHE_ROOT.
	# Example: /home/user/src/cache/github.com_org_repo.git
	setopt localoptions extendedglob
	local cache_root="$1"
	local host="$2"
	local path="$3"

	local base key
	base="$host/${path%.git}"
	key="$base"
	key="${key//\//_}"
	key="${key//:/_}"
	key="${key//@/_}"
	printf "%s/%s.git" "$cache_root" "$key"
}

_git_common_dir_abs() {
	# Prints absolute git common dir for a worktree.
	# Avoids newer git-only flags like --path-format.
	local wt_dir="$1"
	local common
	common=$(git -C "$wt_dir" rev-parse --git-common-dir 2>/dev/null || true)
	if [[ -z "$common" ]]; then
		return 1
	fi
	if [[ "$common" == /* ]]; then
		print -r -- "$common"
		return 0
	fi
	(
		cd "$wt_dir" 2>/dev/null || exit 1
		cd "$common" 2>/dev/null || exit 1
		pwd -P
	)
}

_worktree_common_dir_from_gitfile() {
	# Best-effort common dir derivation from worktree .git file.
	# Prints the bare repo dir (common dir) if it looks like a linked worktree.
	local wt_dir="$1"
	local line gitdir
	line="$(<"$wt_dir/.git" 2>/dev/null)"
	gitdir="${line#gitdir: }"
	gitdir="${gitdir%%$'\n'*}"
	[[ -n "$gitdir" ]] || return 1

	# Resolve relative gitdir if needed.
	if [[ "$gitdir" != /* ]]; then
		(
			cd "$wt_dir" 2>/dev/null || exit 1
			cd "$gitdir" 2>/dev/null || exit 1
			gitdir="$(pwd -P)" || exit 1
			print -r -- "$gitdir"
		) | {
			read -r gitdir
			:
		}
	fi

	if [[ "$gitdir" == */worktrees/* ]]; then
		print -r -- "${gitdir%/worktrees/*}"
		return 0
	fi

	return 1
}

_setup_repo_migrate_worktree_cache() {
	# Migrates an existing worktree to a new bare cache path.
	# Args: expected_bare wt_dir syn_branch base_branch remote_url [current_common_override]
	local expected_bare="$1"
	local wt_dir="$2"
	local syn_branch="$3"
	local base_branch="$4"
	local remote_url="$5"
	local current_common_override="${6:-}"

	local current_common
	current_common="$current_common_override"
	if [[ -z "$current_common" ]]; then
		current_common=$(_worktree_common_dir_from_gitfile "$wt_dir" 2>/dev/null || true)
	fi
	if [[ -z "$current_common" ]]; then
		current_common=$(_git_common_dir_abs "$wt_dir" 2>/dev/null || true)
	fi
	if [[ -z "$current_common" ]]; then
		echo "setup_repo: existing directory is not a git worktree: $wt_dir" >&2
		return 2
	fi

	if [[ "$current_common" == "$expected_bare" ]]; then
		return 0
	fi

	if [[ "${SETUP_REPO_MIGRATE:-}" != "1" ]]; then
		echo "setup_repo: worktree exists but uses a different cache:" >&2
		echo "  worktree:  $wt_dir" >&2
		echo "  current:   $current_common" >&2
		echo "  expected:  $expected_bare" >&2
		echo "" >&2
		echo "To migrate in-place (keeps local commits and local changes), rerun:" >&2
		echo "  SETUP_REPO_MIGRATE=1 setup_repo $remote_url $base_branch" >&2
		return 2
	fi

	echo "Migrating worktree to new cache layout: $wt_dir"

	local tmp
	tmp=$(mktemp -d 2>/dev/null || mktemp -d -t setup_repo)
	local staged_patch="$tmp/staged.patch"
	local unstaged_patch="$tmp/unstaged.patch"
	local untracked_tar="$tmp/untracked.tar"

	local head_sha
	head_sha=$(git -C "$wt_dir" rev-parse HEAD)

	git -C "$wt_dir" diff --cached >"$staged_patch"
	git -C "$wt_dir" diff >"$unstaged_patch"
	(
		cd "$wt_dir" || exit 1
		local -a untracked
		untracked=(${(0)$(git ls-files --others --exclude-standard -z 2>/dev/null || true)})
		if (( ${#untracked[@]} > 0 )); then
			tar -cf "$untracked_tar" -- "${untracked[@]}" 2>/dev/null || true
		fi
	)

	mkdir -p "${expected_bare:h}"
	if [[ ! -d "$expected_bare" ]]; then
		echo "Creating bare cache: $expected_bare"
		if [[ -n "$remote_url" ]]; then
			git clone --bare "$remote_url" "$expected_bare" || return 1
		else
			# If the worktree doesn't have an origin URL, clone from the existing bare.
			git clone --bare "$current_common" "$expected_bare" || return 1
			# Best-effort: keep origin pointing at the original remote.
			local old_origin
			old_origin=$(git --git-dir="$current_common" remote get-url origin 2>/dev/null || true)
			if [[ -n "$old_origin" ]]; then
				git --git-dir="$expected_bare" remote set-url origin "$old_origin" >/dev/null 2>&1 || true
			fi
		fi
		git --git-dir="$expected_bare" config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
		git --git-dir="$expected_bare" config --unset-all remote.origin.mirror 2>/dev/null || true
	fi
	# Fetch only if origin exists.
	if git --git-dir="$expected_bare" remote get-url origin >/dev/null 2>&1; then
		git --git-dir="$expected_bare" fetch origin --prune >/dev/null 2>&1 || true
	fi

	if ! git --git-dir="$expected_bare" cat-file -e "$head_sha^{commit}" 2>/dev/null; then
		echo "Fetching local commits from old cache..."
		git --git-dir="$expected_bare" fetch "$current_common" "$head_sha" >/dev/null 2>&1 || true
	fi
	if ! git --git-dir="$expected_bare" cat-file -e "$head_sha^{commit}" 2>/dev/null; then
		echo "setup_repo: failed to import commit $head_sha from $current_common" >&2
		return 1
	fi

	git --git-dir="$expected_bare" branch -f "$syn_branch" "$head_sha" >/dev/null
	if git --git-dir="$expected_bare" show-ref --verify --quiet "refs/remotes/origin/$base_branch"; then
		git --git-dir="$expected_bare" branch --set-upstream-to "origin/$base_branch" "$syn_branch" >/dev/null 2>&1 || true
	fi

	local wt_name
	wt_name="${wt_dir:t}"
	local new_wt="$tmp/$wt_name"
	git --git-dir="$expected_bare" worktree add "$new_wt" "$syn_branch" >/dev/null
	git -C "$new_wt" config push.default upstream >/dev/null 2>&1 || true
	git -C "$new_wt" config opencode.syntheticWorktrees true >/dev/null 2>&1 || true

	local backup_dir
	backup_dir="${wt_dir}.bak.$(date +%Y%m%d%H%M%S)"
	echo "Moving old worktree to: $backup_dir"
	mv "$wt_dir" "$backup_dir"
	echo "Installing migrated worktree at: $wt_dir"
	mv "$new_wt" "$wt_dir"

	if [[ -s "$staged_patch" ]]; then
		git -C "$wt_dir" apply --index "$staged_patch" || true
	fi
	if [[ -s "$unstaged_patch" ]]; then
		git -C "$wt_dir" apply "$unstaged_patch" || true
	fi
	if [[ -f "$untracked_tar" ]]; then
		( cd "$wt_dir" && tar -xf "$untracked_tar" 2>/dev/null || true )
	fi

	echo "Migration complete. Backup kept at: $backup_dir"
	return 0
}

update_workspace() {
	emulate -L zsh
	setopt nullglob extendedglob

	# Some environments end up emitting variable-assignment trace lines to stdout.
	# Capture stdout, filter those lines, then print clean output.
	local _uw_tmp
	_uw_tmp="/tmp/update_workspace.$$.${RANDOM}.out"
	: >"$_uw_tmp" || return 1

	{

	# Scans the current directory for git worktrees (".git" files) and migrates
	# them to the current cache layout.
	#
	# Dry-run by default.
	# Run with: update_workspace --migrate
	local do_migrate=0
	if [[ "${1:-}" == "--migrate" ]]; then
		do_migrate=1
		shift
	fi

	local cache_root="${CACHE_ROOT:-$HOME/src/cache}"
	local ws
	ws="$(_setup_repo_workspace_name)"
	if [[ -z "$ws" ]]; then
		echo "update_workspace: could not determine workspace name (set WORKSPACE_NAME)" >&2
		return 2
	fi

	local wt
	local found=0
	for wt in *(/N); do
		[[ -f "$wt/.git" ]] || continue

		local current_common
		current_common=$(_worktree_common_dir_from_gitfile "$wt" 2>/dev/null || true)
		[[ -z "$current_common" ]] && current_common=$(_git_common_dir_abs "$wt" 2>/dev/null || true)
		if [[ -z "$current_common" ]]; then
			continue
		fi

		# Only proceed for linked worktrees.
		case "$current_common" in
			"$cache_root"/*) ;;
			*) continue ;;
		esac

		local upstream base_branch
		upstream=$(git -C "$wt" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
		base_branch=""
		if [[ "$upstream" == */* ]]; then
			base_branch="${upstream#*/}"
		else
			# Prefer origin/HEAD from the worktree when upstream isn't set.
			base_branch="$(_setup_repo_worktree_default_branch "$wt" 2>/dev/null || true)"
		fi
		[[ -n "$base_branch" ]] || base_branch="main"

		local syn_branch
		syn_branch="$base_branch@$ws"

		local remote_url
		remote_url=$(git -C "$wt" config --get remote.origin.url 2>/dev/null || true)

		local host remote_path
		if [[ -n "$remote_url" ]]; then
			local parsed
			parsed="$(_setup_repo_parse_remote "$remote_url" 2>/dev/null)" || {
				echo "update_workspace: unsupported remote for $wt: $remote_url" >&2
				continue
			}
			host="${${(f)parsed}[1]}"
			remote_path="${${(f)parsed}[2]}"
		else
			# Fallback: derive host/path from the current bare cache directory.
			# Expected old layouts:
			# - $CACHE_ROOT/<host>/<path>.git
			# - $CACHE_ROOT/git@<host>/<path>.git
			local rel
			rel="${current_common#$cache_root/}"
			if [[ "$rel" == "$current_common" ]]; then
				continue
			fi
			host="${rel%%/*}"
			remote_path="${rel#*/}"
			# Strip user@ if present
			host="${host#*@}"
		fi

		local expected_bare
		expected_bare="$(_setup_repo_expected_bare_dir "$cache_root" "$host" "$remote_path")"

		if [[ "$current_common" == "$expected_bare" ]]; then
			continue
		fi
		found=$((found + 1))

		echo "- $wt"
		if [[ -n "$remote_url" ]]; then
			echo "  remote:   $remote_url"
		else
			echo "  remote:   (none)"
		fi
		echo "  current:  $current_common"
		echo "  expected: $expected_bare"

		if (( do_migrate )); then
			SETUP_REPO_MIGRATE=1 _setup_repo_migrate_worktree_cache \
				"$expected_bare" \
				"$wt" \
				"$syn_branch" \
				"$base_branch" \
				"$remote_url" \
				"$current_common" || return $?
		fi
	done

		if (( ! do_migrate )); then
			if (( found > 0 )); then
				echo "" >&2
				echo "Dry-run only. To migrate all listed worktrees:" >&2
				echo "  update_workspace --migrate" >&2
			else
				echo "update_workspace: nothing to migrate" >&2
			fi
		fi
	} >"$_uw_tmp"

	local _uw_line
	while IFS= read -r _uw_line; do
		case "$_uw_line" in
			[A-Za-z_]*=*) continue ;;
		esac
		print -r -- "$_uw_line"
	done <"$_uw_tmp"

	rm -f "$_uw_tmp" >/dev/null 2>&1 || true
}

setup_repo() {
	local remote_url="$1"
	local branch="${2:-}"
	local cache_root="${CACHE_ROOT:-$HOME/src/cache}"

	if [[ -z "$remote_url" ]]; then
		echo "usage: setup_repo <git-url> [branch]" >&2
		return 2
	fi

	local workspace_name
	workspace_name="$(_setup_repo_workspace_name)"
	if [[ -z "$workspace_name" ]]; then
		echo "setup_repo: could not determine workspace name (set WORKSPACE_NAME)" >&2
		return 2
	fi

	# Parse git remote into host + path (supports scp-style SSH, ssh://, https://)
	local host remote_path
	local parsed
	parsed="$(_setup_repo_parse_remote "$remote_url")" || {
		echo "setup_repo: unsupported git URL: $remote_url" >&2
		return 2
	}
	host="${${(f)parsed}[1]}"
	remote_path="${${(f)parsed}[2]}"

	if [[ "$current_common" == "$expected_bare" ]]; then
		return 0
	fi

	if [[ "${SETUP_REPO_MIGRATE:-}" != "1" ]]; then
		echo "setup_repo: worktree exists but uses a different cache:" >&2
		echo "  worktree:  $wt_dir" >&2
		echo "  current:   $current_common" >&2
		echo "  expected:  $expected_bare" >&2
		echo "" >&2
		echo "To migrate in-place (keeps local commits and local changes), rerun:" >&2
		echo "  SETUP_REPO_MIGRATE=1 setup_repo $remote_url $base_branch" >&2
		return 2
	fi

	echo "Migrating worktree to new cache layout: $wt_dir"

	local tmp
	tmp=$(mktemp -d 2>/dev/null || mktemp -d -t setup_repo)
	local staged_patch="$tmp/staged.patch"
	local unstaged_patch="$tmp/unstaged.patch"
	local untracked_tar="$tmp/untracked.tar"

	local head_sha
	head_sha=$(git -C "$wt_dir" rev-parse HEAD)

	git -C "$wt_dir" diff --cached >"$staged_patch"
	git -C "$wt_dir" diff >"$unstaged_patch"
	(
		cd "$wt_dir" || exit 1
		local -a untracked
		untracked=(${(0)$(git ls-files --others --exclude-standard -z 2>/dev/null || true)})
		if (( ${#untracked[@]} > 0 )); then
			tar -cf "$untracked_tar" -- "${untracked[@]}" 2>/dev/null || true
		fi
	)

	mkdir -p "$(dirname "$expected_bare")"
	if [[ ! -d "$expected_bare" ]]; then
		echo "Creating bare cache: $expected_bare"
		git clone --bare "$remote_url" "$expected_bare" || return 1
		git --git-dir="$expected_bare" config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
		git --git-dir="$expected_bare" config --unset-all remote.origin.mirror 2>/dev/null || true
	fi
	git --git-dir="$expected_bare" fetch origin --prune >/dev/null 2>&1 || true

	if ! git --git-dir="$expected_bare" cat-file -e "$head_sha^{commit}" 2>/dev/null; then
		echo "Fetching local commits from old cache..."
		git --git-dir="$expected_bare" fetch "$current_common" "$head_sha" >/dev/null 2>&1 || true
	fi
	if ! git --git-dir="$expected_bare" cat-file -e "$head_sha^{commit}" 2>/dev/null; then
		echo "setup_repo: failed to import commit $head_sha from $current_common" >&2
		return 1
	fi

	git --git-dir="$expected_bare" branch -f "$syn_branch" "$head_sha" >/dev/null
	if git --git-dir="$expected_bare" show-ref --verify --quiet "refs/remotes/origin/$base_branch"; then
		git --git-dir="$expected_bare" branch --set-upstream-to "origin/$base_branch" "$syn_branch" >/dev/null 2>&1 || true
	fi

	local new_wt="$tmp/new-worktree"
	git --git-dir="$expected_bare" worktree add "$new_wt" "$syn_branch" >/dev/null
	git -C "$new_wt" config push.default upstream >/dev/null 2>&1 || true
	git -C "$new_wt" config opencode.syntheticWorktrees true >/dev/null 2>&1 || true

	local backup_dir
	backup_dir="${wt_dir}.bak.$(date +%Y%m%d%H%M%S)"
	echo "Moving old worktree to: $backup_dir"
	mv "$wt_dir" "$backup_dir"
	echo "Installing migrated worktree at: $wt_dir"
	mv "$new_wt" "$wt_dir"

	if [[ -s "$staged_patch" ]]; then
		git -C "$wt_dir" apply --index "$staged_patch" || true
	fi
	if [[ -s "$unstaged_patch" ]]; then
		git -C "$wt_dir" apply "$unstaged_patch" || true
	fi
	if [[ -f "$untracked_tar" ]]; then
		( cd "$wt_dir" && tar -xf "$untracked_tar" 2>/dev/null || true )
	fi

	echo "Migration complete. Backup kept at: $backup_dir"
	return 0
}

update_workspace() {
	emulate -L zsh
	setopt nullglob globstarshort extendedglob
	# Force-disable tracing even if the parent shell enabled it.
	set +x 2>/dev/null || true

	# Scans the current directory for git worktrees (".git" files) and migrates
	# them to the current cache layout.
	#
	# Dry-run by default.
	# Run with: update_workspace --migrate
	local do_migrate=0
	if [[ "${1:-}" == "--migrate" ]]; then
		do_migrate=1
		shift
	fi

	local cache_root="${CACHE_ROOT:-$HOME/src/cache}"
	local ws
	ws="$(_setup_repo_workspace_name)"
	if [[ -z "$ws" ]]; then
		echo "update_workspace: could not determine workspace name (set WORKSPACE_NAME)" >&2
		return 2
	fi

	local -a gitfiles
	# Only worktrees have a .git *file*; normal clones have a .git directory.
	gitfiles=(**/.git(.N))
	if (( ${#gitfiles[@]} == 0 )); then
		echo "update_workspace: no git worktrees found" >&2
		return 0
	fi

	local gf wt
	local found=0
	for gf in "${gitfiles[@]}"; do
		# Worktrees have a .git *file*; normal clones have a .git directory.
		[[ -f "$gf" ]] || continue
		wt="${gf%/.git}"

		local upstream base_branch
		upstream=$(git -C "$wt" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
		base_branch=""
		if [[ "$upstream" == */* ]]; then
			base_branch="${upstream#*/}"
		fi
		[[ -n "$base_branch" ]] || base_branch="main"

		local syn_branch
		syn_branch="$base_branch@$ws"

		local current_common
		# Prefer reading .git file (cheap; works even if git rev-parse fails).
		current_common=$(_worktree_common_dir_from_gitfile "$wt" 2>/dev/null || true)
		if [[ -z "$current_common" ]]; then
			current_common=$(_git_common_dir_abs "$wt" 2>/dev/null || true)
		fi
		if [[ -z "$current_common" ]]; then
			continue
		fi

		local remote_url
		remote_url=$(git -C "$wt" config --get remote.origin.url 2>/dev/null || true)

		local host path
		if [[ -n "$remote_url" ]]; then
			local parsed
			parsed="$(_setup_repo_parse_remote "$remote_url" 2>/dev/null)" || {
				echo "update_workspace: unsupported remote for $wt: $remote_url" >&2
				continue
			}
			host="${${(f)parsed}[1]}"
			path="${${(f)parsed}[2]}"
		else
			# Fallback: derive host/path from the current bare cache directory.
			# Expected old layouts:
			# - $CACHE_ROOT/<host>/<path>.git
			# - $CACHE_ROOT/git@<host>/<path>.git
			local rel
			rel="${current_common#$cache_root/}"
			if [[ "$rel" == "$current_common" ]]; then
				continue
			fi
			host="${rel%%/*}"
			path="${rel#*/}"
			# Strip user@ if present
			host="${host#*@}"
		fi

		local expected_bare
		expected_bare="$(_setup_repo_expected_bare_dir "$cache_root" "$host" "$path")"

		if [[ "$current_common" == "$expected_bare" ]]; then
			continue
		fi
		found=$((found + 1))

		echo "- $wt"
		if [[ -n "$remote_url" ]]; then
			echo "  remote:   $remote_url"
		else
			echo "  remote:   (none)"
		fi
		echo "  current:  $current_common"
		echo "  expected: $expected_bare"

		if (( do_migrate )); then
			SETUP_REPO_MIGRATE=1 _setup_repo_migrate_worktree_cache \
				"$expected_bare" \
				"$wt" \
				"$syn_branch" \
				"$base_branch" \
				"$remote_url" || return $?
		fi
		done

	if (( ! do_migrate )); then
		if (( found > 0 )); then
			echo "" >&2
			echo "Dry-run only. To migrate all listed worktrees:" >&2
			echo "  update_workspace --migrate" >&2
		else
			echo "update_workspace: nothing to migrate" >&2
		fi
	fi
}

setup_repo() {
	local remote_url="$1"
	local branch="${2:-main}"
	local cache_root="${CACHE_ROOT:-$HOME/src/cache}"

	if [[ -z "$remote_url" ]]; then
		echo "usage: setup_repo <git-url> [branch]" >&2
		return 2
	fi

	local workspace_name
	workspace_name="$(_setup_repo_workspace_name)"
	if [[ -z "$workspace_name" ]]; then
		echo "setup_repo: could not determine workspace name (set WORKSPACE_NAME)" >&2
		return 2
	fi

	# Parse git remote into host + path (supports scp-style SSH, ssh://, https://)
	local host path
	local parsed
	parsed="$(_setup_repo_parse_remote "$remote_url")" || {
		echo "setup_repo: unsupported git URL: $remote_url" >&2
		return 2
	}
	host="${${(f)parsed}[1]}"
	path="${${(f)parsed}[2]}"

	local repo_name
	repo_name=$(basename "$path" .git)
	local bare_dir
	bare_dir="$(_setup_repo_expected_bare_dir "$cache_root" "$host" "$path")"
	local worktree_dir="./$repo_name"

	mkdir -p "${bare_dir:h}"

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
	git --git-dir="$bare_dir" worktree prune >/dev/null 2>&1 || true

	# If no branch was requested, use origin's default branch.
	if [[ -z "$branch" ]]; then
		branch="$(_setup_repo_origin_default_branch "$bare_dir")"
	fi

	local synthetic_branch="${branch}@${workspace_name}"

	local start_ref="origin/$branch"
	if ! git --git-dir="$bare_dir" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
		echo "setup_repo: branch '$branch' not found on origin" >&2
		return 1
	fi

	# Make/update the synthetic branch and set it to track origin/<branch>
	if ! git --git-dir="$bare_dir" show-ref --verify --quiet "refs/heads/$synthetic_branch"; then
		git --git-dir="$bare_dir" branch "$synthetic_branch" "$start_ref" || return 1
	fi
	git --git-dir="$bare_dir" branch --set-upstream-to "$start_ref" "$synthetic_branch" >/dev/null 2>&1 || true

	if [[ -d "$worktree_dir/.git" || -f "$worktree_dir/.git" ]]; then
		_setup_repo_migrate_worktree_cache "$bare_dir" "$worktree_dir" "$synthetic_branch" "$branch" "$remote_url" || return $?
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

# Ensure these helpers are not function-traced (some shells enable that).
functions +t update_workspace setup_repo _setup_repo_parse_remote _setup_repo_expected_bare_dir \
  _setup_repo_origin_default_branch _setup_repo_worktree_default_branch _setup_repo_migrate_worktree_cache \
  _git_common_dir_abs _worktree_common_dir_from_gitfile >/dev/null 2>&1 || true

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
