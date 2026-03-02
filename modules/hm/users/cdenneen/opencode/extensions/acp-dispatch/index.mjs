import fs from "node:fs/promises";
import path from "node:path";
import { GatewayClient } from "openclaw/plugin-sdk/gateway/client.js";

const STATE_FILE = "acp-dispatch.json";
const COMMAND_NAME = "session";

function normalizeAccountId(accountId) {
  return accountId && String(accountId).trim() ? String(accountId).trim() : "default";
}

function normalizeConversationId(conversationId) {
  const value = conversationId && String(conversationId).trim();
  return value ? value : null;
}

function extractConversationIdFromTarget(value) {
  if (!value) return null;
  const raw = String(value).trim();
  if (!raw) return null;
  if (raw.startsWith("telegram:")) return raw.slice("telegram:".length);
  return raw;
}

function resolveConversationId(ctx, metadata) {
  const direct = normalizeConversationId(ctx.conversationId);
  if (direct) return direct;

  const fromTarget = extractConversationIdFromTarget(ctx.from);
  if (fromTarget) return normalizeConversationId(fromTarget);

  const toTarget = extractConversationIdFromTarget(ctx.to);
  if (toTarget) return normalizeConversationId(toTarget);

  const chatId = metadata?.chatId ?? metadata?.chat_id;
  if (chatId != null) return normalizeConversationId(String(chatId));

  return null;
}

function makeBindingKey(ctx, metadata) {
  const accountId = normalizeAccountId(ctx.accountId);
  const conversationId = resolveConversationId(ctx, metadata);
  if (!conversationId) return null;
  return `${ctx.channelId}:${accountId}:${conversationId}`;
}

function resolveGatewayUrl(api) {
  const envUrl = process.env.OPENCLAW_GATEWAY_URL;
  if (envUrl && envUrl.trim()) return envUrl.trim();
  const remoteUrl = api.config?.gateway?.remote?.url;
  if (remoteUrl && String(remoteUrl).trim()) return String(remoteUrl).trim();
  return "ws://127.0.0.1:18789";
}

function resolveGatewayToken() {
  const token = process.env.OPENCLAW_GATEWAY_TOKEN;
  return token && token.trim() ? token.trim() : undefined;
}

function createGatewayClient(api) {
  return new GatewayClient({
    url: resolveGatewayUrl(api),
    token: resolveGatewayToken(),
    clientName: "gateway-client",
    clientDisplayName: "acp-dispatch",
    mode: "backend",
  });
}

function isSessionKey(value) {
  return typeof value === "string" && value.startsWith("agent:");
}

async function loadState(statePath) {
  try {
    const raw = await fs.readFile(statePath, "utf8");
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return { bindings: {}, lastSeen: {} };
    return {
      bindings: typeof parsed.bindings === "object" && parsed.bindings ? parsed.bindings : {},
      lastSeen: typeof parsed.lastSeen === "object" && parsed.lastSeen ? parsed.lastSeen : {},
    };
  } catch (err) {
    if (err && err.code === "ENOENT") return { bindings: {}, lastSeen: {} };
    throw err;
  }
}

async function saveState(statePath, state) {
  await fs.mkdir(path.dirname(statePath), { recursive: true });
  await fs.writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

function buildUsage() {
  return "Usage: /session use <session-key|session-id|session-label> | /session status | /session clear";
}

function resolveMessageId(metadata) {
  const candidates = [
    metadata?.messageId,
    metadata?.message_id,
    metadata?.id,
  ];
  for (const candidate of candidates) {
    if (candidate === undefined || candidate === null) continue;
    const value = String(candidate).trim();
    if (value) return value;
  }
  return null;
}

function shouldSkipContent(content) {
  const trimmed = content.trim();
  if (!trimmed) return true;
  if (!trimmed.startsWith("/")) return false;
  return trimmed.startsWith("/session") || trimmed.startsWith("/acp");
}

function isGroupMessage(metadata) {
  if (!metadata || typeof metadata !== "object") return false;
  if (metadata.chatType === "group" || metadata.chatType === "channel") return true;
  if (metadata.isGroup === true) return true;
  return false;
}

const plugin = {
  id: "acp-dispatch",
  name: "ACP Dispatch",
  description: "Auto-dispatch Telegram DMs to ACP sessions.",
  register(api) {
    const logger = api.logger;
    const statePath = path.join(api.runtime.state.resolveStateDir(api.config), STATE_FILE);
    const allowRepliesUntil = new Map();
    let gatewayClient;

    const resolveSessionKey = async (raw) => {
      if (!raw) return { ok: false, error: "Missing session reference." };
      if (isSessionKey(raw)) return { ok: true, key: raw, label: raw };

      if (!gatewayClient) {
        gatewayClient = createGatewayClient(api);
        gatewayClient.start();
      }

      try {
        const resolved = await gatewayClient.request("sessions.resolve", { label: raw });
        const key = resolved?.key;
        if (typeof key === "string" && key.trim()) {
          return { ok: true, key: key.trim(), label: raw };
        }
        return { ok: false, error: `Unable to resolve session label: ${raw}` };
      } catch (err) {
        return { ok: false, error: `Unable to resolve session label: ${raw}` };
      }
    };

    api.registerCommand({
      name: COMMAND_NAME,
      description: "Bind this conversation to an ACP session.",
      acceptsArgs: true,
      handler: async (ctx) => {
        if (!ctx.isAuthorizedSender) {
          return { text: "Not authorized." };
        }

        const key = makeBindingKey(ctx);
        if (!key) {
          return { text: "Unable to resolve conversation id for binding." };
        }

        const args = (ctx.args ?? "").trim();
        if (!args) {
          return { text: buildUsage() };
        }

        const [actionRaw, ...rest] = args.split(/\s+/);
        const action = actionRaw?.toLowerCase();
        const target = rest.join(" ").trim();

        const state = await loadState(statePath);
        if (action === "use") {
          if (!target) return { text: buildUsage() };
          const resolved = await resolveSessionKey(target);
          if (!resolved.ok) return { text: resolved.error };
          state.bindings[key] = {
            target,
            sessionKey: resolved.key,
            boundAt: Date.now(),
          };
          await saveState(statePath, state);
          allowRepliesUntil.set(key, Date.now() + 5000);
          return { text: `Bound ACP session to this chat: ${target}` };
        }

        if (action === "status") {
          const binding = state.bindings[key];
          if (!binding) return { text: "No ACP session bound for this chat." };
          const resolved = await resolveSessionKey(binding.target ?? binding.sessionKey);
          if (!resolved.ok) return { text: resolved.error };
          return { text: `ACP session bound: ${binding.target} (${resolved.key})` };
        }

        if (action === "clear") {
          if (state.bindings[key]) {
            delete state.bindings[key];
            await saveState(statePath, state);
            allowRepliesUntil.set(key, Date.now() + 5000);
            return { text: "Cleared ACP session binding for this chat." };
          }
          return { text: "No ACP session bound for this chat." };
        }

        return { text: buildUsage() };
      },
    });

    api.registerHook("message_received", async (event, ctx) => {
      if (ctx.channelId !== "telegram") return;
      if (isGroupMessage(event.metadata)) return;
      if (shouldSkipContent(event.content ?? "")) return;

      let state;
      try {
        state = await loadState(statePath);
      } catch (err) {
        logger.warn(`acp-dispatch: failed to load state: ${err instanceof Error ? err.message : String(err)}`);
        return;
      }

      const key = makeBindingKey(ctx, event.metadata);
      if (!key) return;
      const binding = state.bindings[key];
      if (!binding?.target && !binding?.sessionKey) return;

      const resolved = await resolveSessionKey(binding.sessionKey ?? binding.target);
      if (!resolved.ok) {
        logger.warn(`acp-dispatch: ${resolved.error}`);
        return;
      }

      const messageId = resolveMessageId(event.metadata);
      if (messageId && state.lastSeen[key] === messageId) return;

      state.lastSeen[key] = messageId ?? String(Date.now());
      try {
        await saveState(statePath, state);
      } catch (err) {
        logger.warn(`acp-dispatch: failed to persist state: ${err instanceof Error ? err.message : String(err)}`);
      }

      const chatId = resolveConversationId(ctx, event.metadata);
      if (!chatId) return;
      const to = `telegram:${chatId}`;
      const body = String(event.content ?? "").trim();

      const ctxPayload = api.runtime.channel.reply.finalizeInboundContext({
        Body: body,
        BodyForAgent: body,
        RawBody: body,
        CommandBody: body,
        From: to,
        To: to,
        SessionKey: resolved.key,
        AccountId: ctx.accountId,
        ChatType: "direct",
        ConversationLabel: `telegram:${chatId}`,
        SenderId: event.from,
        Provider: "telegram",
        Surface: "telegram",
        MessageSid: messageId ?? void 0,
        OriginatingChannel: "telegram",
        OriginatingTo: to,
      });

      try {
        await api.runtime.channel.reply.dispatchReplyWithBufferedBlockDispatcher({
          ctx: ctxPayload,
          cfg: api.config,
          dispatcherOptions: {
            deliver: async (payload) => {
              const text = payload.text ?? "";
              const mediaUrl = payload.mediaUrl ?? payload.mediaUrls?.[0];
              if (!text && !mediaUrl) return;

              const replyToMessageId = messageId ? Number.parseInt(messageId, 10) : NaN;

              await api.runtime.channel.telegram.sendMessageTelegram(chatId, text, {
                accountId: ctx.accountId,
                mediaUrl,
                replyToMessageId: Number.isFinite(replyToMessageId) ? replyToMessageId : void 0,
                asVoice: payload.audioAsVoice ?? false,
                asVideoNote: payload.videoAsVideoNote ?? false,
              });
            },
            onError: (err, info) => {
              logger.warn(`acp-dispatch: delivery error (${info?.kind ?? "unknown"}): ${err instanceof Error ? err.message : String(err)}`);
            },
          },
        });
      } catch (err) {
        logger.warn(`acp-dispatch: dispatch failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    });

    api.registerHook("message_sending", async (event, ctx) => {
      if (ctx.channelId !== "telegram") return;

      const key = makeBindingKey(ctx, event.metadata);
      if (!key) return;

      const state = await loadState(statePath);
      const binding = state.bindings[key];
      if (!binding?.target && !binding?.sessionKey) return;

      const allowUntil = allowRepliesUntil.get(key);
      if (allowUntil && allowUntil > Date.now()) return;

      return { cancel: true };
    });
  },
};

export default plugin;
