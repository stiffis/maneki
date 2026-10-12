import { connect } from "node:net"
import { join } from "node:path"

const runtime = process.env.XDG_RUNTIME_DIR || `/tmp/perch-${process.getuid()}`
const socketPath = join(runtime, "perch", "perch.sock")

const talk = (message, wantsReply) =>
  new Promise((resolve) => {
    const socket = connect(socketPath)
    let buffer = ""
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      socket.destroy()
      resolve(value)
    }
    socket.on("connect", () => {
      socket.write(JSON.stringify(message) + "\n")
      if (!wantsReply) socket.end(() => finish(null))
    })
    socket.on("data", (chunk) => {
      buffer += chunk
      const end = buffer.indexOf("\n")
      if (end < 0) return
      try {
        finish(JSON.parse(buffer.slice(0, end)))
      } catch {
        finish(null)
      }
    })
    socket.on("error", () => finish(null))
    socket.on("close", () => finish(null))
  })

const replies = { allow: "once", always: "always", deny: "reject" }

export const PerchPlugin = async ({ client, directory }) => {
  const parents = new Map()
  const kinds = new Map()
  const busy = new Set()
  const launches = new Map()

  const event = (sessionID, name, extra = {}) => {
    const parent = parents.get(sessionID)
    return {
      hook_event_name: name,
      session_id: parent ?? sessionID,
      cwd: directory,
      agent: "opencode",
      pid: process.pid,
      ts: Date.now() / 1000,
      ...(parent ? { agent_id: sessionID, agent_type: kinds.get(sessionID) } : {}),
      ...extra,
    }
  }
  const emit = (sessionID, name, extra) => talk({ type: "event", event: event(sessionID, name, extra) }, false)

  const started = (info) => {
    if (!info?.id) return
    if (info.parentID) {
      parents.set(info.id, info.parentID)
      const queue = launches.get(info.parentID) ?? []
      kinds.set(info.id, queue.shift()?.subagent_type || "general")
      emit(info.id, "SubagentStart")
    } else {
      emit(info.id, "SessionStart", { source: "startup" })
    }
  }

  const status = (sessionID, type) => {
    if (type === "busy" && !busy.has(sessionID)) {
      busy.add(sessionID)
      if (!parents.has(sessionID)) emit(sessionID, "UserPromptSubmit")
    } else if (type === "idle" && busy.has(sessionID)) {
      busy.delete(sessionID)
      emit(sessionID, parents.has(sessionID) ? "SubagentStop" : "Stop")
    }
  }

  const answer = async (request, choice) => {
    const reply = replies[choice]
    if (!reply) return
    try {
      if (client.permission?.reply) {
        await client.permission.reply({ requestID: request.id, reply, directory })
      } else if (client.postSessionIdPermissionsPermissionId) {
        await client.postSessionIdPermissionsPermissionId({
          path: { id: request.sessionID, permissionID: request.id },
          body: { response: reply },
        })
      }
    } catch {}
  }

  const asked = async (request) => {
    const patterns = request.patterns ?? (request.pattern ? [].concat(request.pattern) : [])
    const tool = request.permission ?? request.type ?? "permission"
    const data = { ...(request.metadata ?? {}), patterns }
    if (tool === "bash" && !data.command && patterns.length) data.command = patterns.join("\n")
    const message = {
      type: "ask",
      event: event(request.sessionID, "PermissionRequest", {
        tool_name: tool,
        tool_input: data,
        tool_use_id: request.id,
        permission_suggestions: [{ type: "opencode", patterns: request.always?.length ? request.always : patterns }],
      }),
    }
    const reply = await talk(message, true)
    if (reply?.answer) await answer(request, reply.answer)
  }

  return {
    event: async ({ event: incoming }) => {
      const props = incoming.properties ?? {}
      switch (incoming.type) {
        case "session.created":
          started(props.info)
          break
        case "session.status":
          status(props.sessionID, props.status?.type)
          break
        case "session.idle":
          status(props.sessionID, "idle")
          break
        case "session.error":
          if (props.sessionID && !parents.has(props.sessionID)) {
            emit(props.sessionID, "StopFailure", { error: String(props.error?.data?.message ?? props.error?.name ?? "error") })
          }
          break
        case "session.deleted":
          if (props.info?.id && !parents.has(props.info.id)) emit(props.info.id, "SessionEnd")
          break
        case "permission.asked":
        case "permission.updated":
          asked(props)
          break
        case "question.asked":
          if (props.sessionID) emit(props.sessionID, "PreToolUse", { tool_name: "AskUserQuestion", tool_input: {}, tool_use_id: props.id })
          break
        case "question.replied":
        case "question.rejected":
          if (props.sessionID) emit(props.sessionID, "PostToolUse", { tool_name: "AskUserQuestion", tool_input: {}, tool_use_id: props.requestID ?? props.id })
          break
        case "permission.replied":
          emit(props.sessionID, "PermissionResolved", { tool_use_id: props.requestID ?? props.permissionID })
          break
      }
    },
    "tool.execute.before": async (input, output) => {
      if (input.tool === "task") {
        const queue = launches.get(input.sessionID) ?? []
        queue.push(output.args ?? {})
        launches.set(input.sessionID, queue)
      }
      emit(input.sessionID, "PreToolUse", { tool_name: input.tool, tool_input: output.args ?? {}, tool_use_id: input.callID })
    },
    "tool.execute.after": async (input) => {
      emit(input.sessionID, "PostToolUse", { tool_name: input.tool, tool_input: input.args ?? {}, tool_use_id: input.callID })
    },
  }
}
