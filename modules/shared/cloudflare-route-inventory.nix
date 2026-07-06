let
  route =
    {
      hostname,
      owner,
      serviceByTunnel,
      path ? null,
    }:
    {
      inherit
        hostname
        owner
        serviceByTunnel
        ;
    }
    // (if path == null then { } else { inherit path; });
in
{
  # Cloudflare-published tunnel ingress and DNS ownership inventory.
  #
  # This is intentionally not used as the direct source for the host-local
  # `services.cloudflared.tunnels.<id>.ingress` definitions because the NixOS
  # module cannot represent multiple path rules for the same hostname.
  accountId = "19a23ecf9ba79236ab8e64c8c7bf3507";
  zoneName = "denneen.net";

  dnsDefaults = {
    proxied = true;
    ttl = 1;
    type = "CNAME";
  };

  tunnels = {
    nyx.id = "d1d49353-ddca-4c9c-bc8a-3bbb1885aa98";
    ghost.id = "1481e71c-a53f-4fe0-8983-468a3e0fffdf";
    mac.id = "53b1d5f2-afa2-4269-81d4-b01c1037298b";
  };

  publishedRoutes = {
    chat = route {
      hostname = "chat.denneen.net";
      owner = "nyx";
      serviceByTunnel = {
        nyx = "http://localhost:4096";
      };
    };

    opensync = route {
      hostname = "opensync.denneen.net";
      owner = "nyx";
      serviceByTunnel = {
        nyx = "http://localhost:5173";
      };
    };

    ai-dev-api-backend = route {
      hostname = "ai-dev.denneen.net";
      path = "^/api/backend";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:3000";
      };
    };

    ai-dev-ws = route {
      hostname = "ai-dev.denneen.net";
      path = "^/api/v1/ws";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8000";
      };
    };

    ai-dev-api = route {
      hostname = "ai-dev.denneen.net";
      path = "^/api";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8000";
      };
    };

    ai-dev-root = route {
      hostname = "ai-dev.denneen.net";
      path = "^/";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:3000";
      };
    };

    ai-api-backend = route {
      hostname = "ai.denneen.net";
      path = "^/api/backend";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:3001";
      };
    };

    ai-ws = route {
      hostname = "ai.denneen.net";
      path = "^/api/v1/ws";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8001";
      };
    };

    ai-api = route {
      hostname = "ai.denneen.net";
      path = "^/api";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8001";
      };
    };

    ai-root = route {
      hostname = "ai.denneen.net";
      path = "^/";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:3001";
      };
    };

    peps = route {
      hostname = "peps.denneen.net";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8787";
      };
    };

    peps-api = route {
      hostname = "peps-api.denneen.net";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8787";
      };
    };

    wellness-api = route {
      hostname = "wellness-api.denneen.net";
      owner = "ghost";
      serviceByTunnel = {
        ghost = "http://localhost:8797";
      };
    };
  };
}
