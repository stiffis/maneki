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

const effects = { allow: "allow", always: "allow", deny: "deny" }

export default {
  id: "perch",
  setup: async (context) => {
    const directory = context.location?.directory
    const parents = new Map()
    const busy = new Set()
    const handles = []

    const parentOf = async (sessionID) => {
      if (!parents.has(sessionID)) {
        let parent = null
        try {
          const session = await context.session.get({ sessionID })
          parent = session?.parentID ?? session?.data?.parentID ?? null
        } catch {}
        parents.set(sessionID, parent)
        if (parent) emit(sessionID, "SubagentStart")
      }
      return parents.get(sessionID)
    }

    const payload = (sessionID, extra = {}) => {
      const parent = parents.get(sessionID)
      return {
        session_id: parent ?? sessionID,
        cwd: directory,
        ...(parent ? { agent_id: sessionID, agent_type: "subagent" } : {}),
        ...extra,
      }
    }
    const emit = (sessionID, name, extra) => hook(name, payload(sessionID, extra), false)

    const working = async (sessionID) => {
      if (!sessionID || busy.has(sessionID)) return
      busy.add(sessionID)
      const parent = await parentOf(sessionID)
      if (!parent) emit(sessionID, "UserPromptSubmit")
      Promise.resolve(context.session.wait({ sessionID }))
        .catch(() => {})
        .finally(() => {
          busy.delete(sessionID)
          emit(sessionID, parent ? "SubagentStop" : "Stop")
        })
    }

    const register = async (make) => {
      try {
        handles.push(await make())
      } catch {}
    }

    await register(() =>
      context.session.hook("context", async (input) => {
        working(input?.sessionID)
      }),
    )
    await register(() =>
      context.tool.hook("execute.before", async (input) => {
        await working(input.sessionID)
        emit(input.sessionID, "PreToolUse", { tool_name: input.tool, tool_input: input.input ?? {}, tool_use_id: input.id })
      }),
    )
    await register(() =>
      context.tool.hook("execute.after", async (input) => {
        const name = input.status === "error" ? "PostToolUseFailure" : "PostToolUse"
        emit(input.sessionID, name, { tool_name: input.tool, tool_input: input.input ?? {}, tool_use_id: input.id })
      }),
    )
    await register(() =>
      context.permission.hook("evaluate", async (input) => {
        if (input.effect !== "ask") return
        await parentOf(input.sessionID)
        const resources = input.resources ?? []
        const data = { ...(input.metadata ?? {}), patterns: resources }
        if ((input.action === "bash" || input.action === "shell") && !data.command && resources.length) data.command = resources.join("\n")
        const result = await hook(
          "PermissionRequest",
          payload(input.sessionID, {
            tool_name: input.action,
            tool_input: data,
            tool_use_id: input.source?.id,
          }),
          true,
        )
        const effect = effects[result?.answer]
        if (effect) input.effect = effect
      }),
    )

    return async () => {
      handles.forEach((handle) => handle?.dispose?.())
      await Promise.all([...busy].map((sessionID) => emit(sessionID, parents.get(sessionID) ? "SubagentStop" : "Stop")))
      busy.clear()
    }
  },
}
