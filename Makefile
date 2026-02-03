NIXNAME ?= $(shell hostname)
FLAKE ?= .

.PHONY: help build switch

help:
	@echo "Usage:"
	@echo "  make build [NIXNAME=host]   # build system configuration"
	@echo "  make switch [NIXNAME=host]  # build and switch system configuration"

build:
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
	@echo "==> Switching configuration for $(NIXNAME)"
	@if [ "$(shell uname)" = "Darwin" ]; then \
		sudo darwin-rebuild switch --flake $(FLAKE)#$(NIXNAME); \
	else \
		sudo nixos-rebuild switch --flake $(FLAKE)#$(NIXNAME); \
	fi
