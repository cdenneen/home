# shellcheck shell=bash

function fancy_echo() {
	local fmt="$1"
	shift

	# shellcheck disable=SC2059
	printf "\\n$fmt\\n" "$@"
}

function pause() {
	read -n 1 -p "Click any key to continue..." -s -e -r
}

function install_xcode() {
	fancy_echo "Installing XCode..."

	if [ "$(uname -s)" = "Darwin" ]; then
		if [ -d "/Applications/Xcode.app" ]; then
			return
		fi
		xcode-select --install
	fi
}

function install_brew() {
	fancy_echo "Setup Homebrew..."

	if [ "$(uname -s)" = "Darwin" ]; then
		HOME_BREW_PREFIX="/opt/homebrew"
	else
		HOME_BREW_PREFIX="/home/linuxbrew/.linuxbrew"
	fi
	if [ ! -d "$HOME_BREW_PREFIX" ]; then
		fancy_echo "Installing Homebrew..."
		/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install.sh)"
	fi
	if ! command -v brew >/dev/null; then
		fancy_echo "Adding Homebrew to PATH..."
		eval "$(${HOME_BREW_PREFIX}/bin/brew shellenv)"
	fi
	for f in gh jq; do
		if ! command -v "$f" >/dev/null; then
			fancy_echo "Installing $f..."
			brew install "$f"
			pause
		fi
	done
}

function install_op() {
	fancy_echo "Installing 1Password CLI..."

	if [ "$(uname -s)" = "Darwin" ]; then
		brew install 1password-cli
	else
		if [ ! -f /usr/share/keyrings/1password-archive-keyring.gpg ]; then
			curl -sS https://downloads.1password.com/linux/keys/1password.asc | sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg
		fi
		if [ ! -f /etc/apt/sources.list.d/1password.list ]; then
			if [ "$(uname -m)" = "arm64" ]; then
				echo 'deb [arch=arm64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/arm64 stable main' | sudo tee /etc/apt/sources.list.d/1password.list >/dev/null
			else
				echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main' | sudo tee /etc/apt/sources.list.d/1password.list >/dev/null
			fi
		fi
		sudo apt update
		sudo apt install 1password-cli gh jq
	fi
}

function get() {
	f="${3:-notesPlain}"
	op item get "$2" --account "$1" --fields "$f" --format json | jq -rM '.value'
}
function getfile() {
	f="${4:-notesPlain}"
	op --account "$1" read "op://$2/$3/$f"
}
function account() {
	domain="${3:-my}.1password.com"
	op account add \
		--address "$domain" \
		--email "$2" \
		--shorthand "$1"
}

function install_setup_op() {
	fancy_echo "Setting up op..."

	if [ -f "$HOME/.config/op/config" ]; then
		return
	else
		account my "chris@denneen.net"
	fi
	eval "$(op signin --account my)"
}

function setup_keychain() {
	fancy_echo "Setting up keychain..."

	if [ ! -f "$HOME/.config/gh/config.yml" ]; then
		get my GH_TOKEN | gh auth login -p ssh --with-token
		mkdir -p "$HOME/.ssh"
		op item get id_ed25519_github --fields privateKey --format json | jq -rM '.ssh_formats.openssh.value' >"$HOME/.ssh/id_ed25519"
		ssh-keyscan -p 22 -H github.com gist.github.com >"$HOME/.ssh/known_hosts"
		chmod 700 "$HOME/.ssh"
		chmod 600 "$HOME/.ssh/id_ed25519"
	fi
}

function brew_bundle() {
	fancy_echo "Updating Homebrew formulae ..."
	if [ ! -f "$HOME"/Brewfile ]; then
		curl -fsSL https://raw.githubusercontent.com/cdenneen/home/main/Brewfile -o "$HOME/Brewfile"
		brew bundle --file="$HOME/Brewfile"
	fi
	if [ "$(uname -s)" = "Darwin" ] && [ ! -f "$HOME/Brewfile-mac" ]; then
		curl -fsSL https://raw.githubusercontent.com/cdenneen/home/main/Brewfile-mac -o "$HOME/Brewfile-mac"
		brew bundle --file="$HOME/Brewfile-mac"
	fi
}

function install_linux_packages() {
	if [ "$(uname -s)" = "Linux" ]; then
		fancy_echo "Setting up linux packages ..."
		curl -fsSL https://raw.githubusercontent.com/cdenneen/home/main/linux-packages.sh | sudo bash
	else
		return
	fi
}

function main() {
	install_xcode
	install_brew
	install_op
	install_setup_op
	setup_keychain
	brew_bundle
	install_linux_packages

	fancy_echo "All done!"
}

main
