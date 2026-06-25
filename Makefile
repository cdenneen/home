NIXNAME ?= $(shell hostname)
FLAKE ?= .
NIXNAME ?=
HM_USER ?= cdenneen

.PHONY: help build switch home-build home-switch fmt check

help:
	@echo "Usage:"
	@echo "  make build NIXNAME=host            # build system configuration"
	@echo "  make switch NIXNAME=host           # switch system configuration"
	@echo "  make home-build [HM_USER=user]     # build Home Manager config"
	@echo "  make home-switch [HM_USER=user]    # switch Home Manager config"
	@echo "  make fmt                           # run repo formatter"
	@echo "  make check                         # run flake checks (fast)"

build:
	@test -n "$(NIXNAME)" || (echo "NIXNAME is required" && exit 1)
	@echo "==> Building configuration for $(NIXNAME)"
	@if [ "$(shell uname)" = "Darwin" ]; then \
		darwin-rebuild build \
		  --option builders-use-substitutes true \
		  --option substitute true \
		  --flake $(FLAKE)#$(NIXNAME); \
	else \
		sudo nixos-rebuild build --flake $(FLAKE)#$(NIXNAME); \
	fi

switch:
	@test -n "$(NIXNAME)" || (echo "NIXNAME is required" && exit 1)
	@echo "==> Switching configuration for $(NIXNAME)"
	@if [ "$(shell uname)" = "Darwin" ]; then \
		sudo darwin-rebuild switch --flake $(FLAKE)#$(NIXNAME); \
	else \
		sudo nixos-rebuild switch --flake $(FLAKE)#$(NIXNAME); \
	fi

home-build:
	@echo "==> Building Home Manager configuration for $(HM_USER)"
	@home-manager build --flake $(FLAKE)#$(HM_USER)

home-switch:
	@echo "==> Switching Home Manager configuration for $(HM_USER)"
	@home-manager switch --flake $(FLAKE)#$(HM_USER)

fmt:
	@echo "==> Running nix fmt"
	@nix fmt

check:
	@echo "==> Running flake checks"
	@nix flake check --accept-flake-config
