import { spawn } from "node:child_process"

const PERCH = "__PERCH_BIN__"

const hook = (name, payload, wantsAnswer) =>
  new Promise((resolve) => {
    let output = ""
    let child
    try {
      child = spawn(PERCH, ["hook", "--agent", "opencode", "--event", name], {
        stdio: ["pipe", wantsAnswer ? "pipe" : "ignore", "ignore"],
      })
    } catch {
      resolve(null)
      return
    }
    child.on("error", () => resolve(null))
    if (wantsAnswer) child.stdout.on("data", (chunk) => (output += chunk))
    child.on("close", () => {
      try {
        resolve(output ? JSON.parse(output) : null)
      } catch {
        resolve(null)
      }
    })
    child.stdin.on("error", () => {})
    child.stdin.end(JSON.stringify(payload))
  })

const replies = { allow: "once", always: "always", deny: "reject" }

export const PerchPlugin = async ({ client, directory }) => {
  const parents = new Map()
  const kinds = new Map()
  const busy = new Set()
  const launches = new Map()

  const payload = (sessionID, extra = {}) => {
    const parent = parents.get(sessionID)
    return {
      session_id: parent ?? sessionID,
      cwd: directory,
      ...(parent ? { agent_id: sessionID, agent_type: kinds.get(sessionID) } : {}),
      ...extra,
    }
  }
  const emit = (sessionID, name, extra) => hook(name, payload(sessionID, extra), false)

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
    const result = await hook(
      "PermissionRequest",
      payload(request.sessionID, {
        tool_name: tool,
        tool_input: data,
        tool_use_id: request.id,
        permission_suggestions: [{ type: "opencode", patterns: request.always?.length ? request.always : patterns }],
      }),
      true,
    )
    if (result?.answer) await answer(request, result.answer)
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
        case "question.asked":
          if (props.sessionID) emit(props.sessionID, "PreToolUse", { tool_name: "AskUserQuestion", tool_input: {}, tool_use_id: props.id })
          break
        case "question.replied":
        case "question.rejected":
          if (props.sessionID) emit(props.sessionID, "PostToolUse", { tool_name: "AskUserQuestion", tool_input: {}, tool_use_id: props.requestID ?? props.id })
          break
        case "permission.asked":
        case "permission.updated":
          asked(props)
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
