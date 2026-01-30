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

function update_secrets() {
  local secrets_file="$HOME/.secrets"

  {
    echo "export AZURE_DEFAULT_USERNAME=\"$(op read 'op://private/office 365/username')\""
    echo "export AZURE_DEFAULT_PASSWORD=\"$(op read 'op://private/office 365/password')\""
    echo "export GITLAB_TOKEN=\"$(op read 'op://private/gitlab personal access token/token')\""
    echo "export TF_HTTP_PASSWORD=\"$(op read 'op://private/gitlab personal access token/token')\""
    echo "export TF_HTTP_USERNAME=\"$(op read 'op://private/gitlab personal access token/username')\""
    echo "export TF_VAR_gitlab_token=\"$(op read 'op://gss/GitLab_tfadmin-cdenneen/credential')\""
    echo "export CI_REGISTRY_USER=\"$(op read 'op://Amazon Web Services/JFrog_gitlabci/username')\""
    echo "export CI_REGISTRY_PASSWORD=\"$(op read 'op://Amazon Web Services/JFrog_gitlabci/credential')\""
    echo "export GI_RENOVATE_TOKEN=\"$(op read 'op://gss/GitLab_gi-renovate/credential')\""
    echo "export RENOVATE_TOKEN=\"$(op read 'op://gss/GitLab_renovate-runner-ci/credential')\""
    echo "export DOCKER_HUB_PASSWORD=\"$(op read 'op://gss/DockerHub_token/credential')\""
    #echo "export GITHUB_TOKEN=\"$(op read 'op://gss/GitHub_gitops-token/token')\""
    echo "export GITHUB_TOKEN=\"$(op read 'op://private/GH_TOKEN/token' --account my)\""
    echo "export OPENAI_API_KEY=\"$(op read 'op://private/OpenAI/credential')\""
  } > "$secrets_file"

  echo "Secrets have been updated in $secrets_file"
}
