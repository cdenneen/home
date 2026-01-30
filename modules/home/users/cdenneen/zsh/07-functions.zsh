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

function update_secrets() {
	local secrets_file="$HOME/.secrets"
	local ap="N6PZN7GSBNDJVIPTSJPHHMKVZA"
	local my="MWL6B6AUDRD77NKKKYII55VDEU"
	local quiet=0
	[[ "$1" == "--quiet" ]] && quiet=1

	# Skip if op is locked or unavailable
	if ! is_op_unlocked; then
		(( quiet )) || echo "1Password locked or unavailable; skipping secrets update."
		return
	fi

	{
		echo "export AZURE_DEFAULT_USERNAME=\"$(op read 'op://private/office 365/username' --account \"$ap\" 2>/dev/null)\""
		echo "export AZURE_DEFAULT_PASSWORD=\"$(op read 'op://private/office 365/password' --account \"$ap\" 2>/dev/null)\""
		echo "export GITLAB_TOKEN=\"$(op read 'op://private/gitlab personal access token/token' --account \"$ap\" 2>/dev/null)\""
		echo "export TF_HTTP_PASSWORD=\"$(op read 'op://private/gitlab personal access token/token' --account \"$ap\" 2>/dev/null)\""
		echo "export TF_HTTP_USERNAME=\"$(op read 'op://private/gitlab personal access token/username' --account \"$ap\" 2>/dev/null)\""
		echo "export TF_VAR_gitlab_token=\"$(op read 'op://gss/GitLab_tfadmin-cdenneen/credential' --account \"$ap\" 2>/dev/null)\""
		echo "export CI_REGISTRY_USER=\"$(op read 'op://Amazon Web Services/JFrog_gitlabci/username' --account \"$ap\" 2>/dev/null)\""
		echo "export CI_REGISTRY_PASSWORD=\"$(op read 'op://Amazon Web Services/JFrog_gitlabci/credential' --account \"$ap\" 2>/dev/null)\""
		echo "export GI_RENOVATE_TOKEN=\"$(op read 'op://gss/GitLab_gi-renovate/credential' --account \"$ap\" 2>/dev/null)\""
		echo "export RENOVATE_TOKEN=\"$(op read 'op://gss/GitLab_renovate-runner-ci/credential' --account \"$ap\" 2>/dev/null)\""
		echo "export DOCKER_HUB_PASSWORD=\"$(op read 'op://gss/DockerHub_token/credential' --account \"$ap\" 2>/dev/null)\""
		echo "export GITHUB_TOKEN=\"$(op read 'op://private/GH_TOKEN/token' --account \"$my\" 2>/dev/null)\""
		echo "export OPENAI_API_KEY=\"$(op read 'op://private/OpenAI/credential' --account \"$ap\" 2>/dev/null)\""
	} > "$secrets_file".tmp

	# Only replace if something actually changed
	if ! cmp -s "$secrets_file".tmp "$secrets_file" 2>/dev/null; then
		mv "$secrets_file".tmp "$secrets_file"
		(( quiet )) || echo "Secrets updated in $secrets_file"
	else
		rm -f "$secrets_file".tmp
	fi
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
