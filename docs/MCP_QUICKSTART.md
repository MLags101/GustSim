# GustSim MCP quick start

This guide connects an AI assistant (Claude Code, Claude Desktop, Cursor or any MCP client) to GustSim, so the AI can run CFD on your robot designs. You do the setup once. After that, steps 1 and 5 are all you need.

For the full tool reference, see [AGENT.md](AGENT.md).

## What you need

- **Docker Desktop** on Windows with the WSL2 engine, or Docker Engine on Ubuntu.
- **Python 3.11 or newer** on the same computer. Check with `python --version`; on Windows, `py -3 --version` also works. No packages need installing.
- This GustSim folder. The examples use `C:\Users\Micha\Desktop\Other\Code\GustSim`; change the path if yours differs.

## 1. Start GustSim (every time)

1. **Turn on Docker Desktop.** Wait until it says *Engine running*. Nothing below works while Docker is off.
2. Open a terminal in the GustSim folder and run:

   ```sh
   docker compose up
   ```

   The first run builds the images and takes a while. Later starts take seconds.
3. Open <http://localhost:8080> in a browser. When the workbench loads, GustSim is up.

Leave that terminal open. GustSim keeps running until you stop it (step 7).

## 2. Connect your AI (once)

### Claude Code

Run this once in any terminal. `--scope user` makes GustSim available in every project, including your robot-design repository.

```sh
claude mcp add --transport stdio --scope user --env PYTHONPATH=C:\Users\Micha\Desktop\Other\Code\GustSim\backend --env GUSTSIM_URL=http://127.0.0.1:8080 gustsim -- python -m gustsim mcp
```

Then type `/mcp` inside Claude Code. `gustsim` should show as **connected**.

### Claude Desktop

1. Open **Settings → Developer → Edit Config**. This opens `claude_desktop_config.json`.
2. Add this block. If the file already has an `mcpServers` section, add only the `"gustsim"` entry to it.

   ```json
   {
     "mcpServers": {
       "gustsim": {
         "command": "python",
         "args": ["-m", "gustsim", "mcp"],
         "env": {
           "PYTHONPATH": "C:\\Users\\Micha\\Desktop\\Other\\Code\\GustSim\\backend",
           "GUSTSIM_URL": "http://127.0.0.1:8080"
         }
       }
     }
   }
   ```

3. **Fully quit Claude Desktop and reopen it.** Closing the window is not enough.
4. In a chat, open the tools menu and make sure **gustsim is switched on**.

Cursor and other MCP clients take the same JSON block in their MCP settings.

## 3. Check the connection

Ask the AI:

> Call gustsim_status and tell me whether the compute worker is online.

The status reply has two parts to check:
- **The worker shows online:** you're ready.
- **The worker shows offline:** Docker or the `worker` container isn't running. Go back to step 1.

## 4. Get your design files ready

- Export your robot as **STEP** (best: it carries its own units), or as STL, OBJ, GLB or IGES.
- **Save it on this computer.** The AI imports by file path.
- **Know the units of mesh files.** STL, OBJ and GLB files carry no units, so tell the AI whether they are mm or m.
- **Name the parts.** Separate bodies (chassis, propeller, arm) can then be measured or spun separately.

## 5. Tell the AI what you want

Paste a prompt like this one and fill in the brackets.

> Use the GustSim MCP tools. Follow the design loop in docs/AGENT.md.
>
> - **Geometry:** `[C:\path\to\robot.step]` (units: `[mm]`)
> - **Fluid:** `[air / water]`
> - **Speed:** `[5] m/s`
> - **Travel direction:** `[+X]`
> - **Robot body axes:** forward = `[+X]`, up = `[+Z]`
> - **What to find:** `[drag and pitch moment in body axes / thrust and torque of the propeller at 6000 rpm / which of these 3 designs has the lowest drag]`
> - **Budget:** start with the `basic` tier and check `gustsim_estimate` before running. Ask me before anything over `[30] minutes`.
>
> Show me every review finding before you acknowledge it. Report each number with its status: execution, numerical quality and validation.

More request ideas:
- *"Sweep the speed from 2 to 10 m/s and give me drag versus speed."*
- *"Pitch the drone from −10° to +10° in 5° steps."*
- *"Make a thrust map of this propeller for 4000–8000 rpm at 0, 4 and 8 m/s."*
- *"Compare design_A.step and design_B.step at the same setup."*

## 6. Understand the answers

| The AI says | Meaning |
|---|---|
| `needs_acknowledgement` | GustSim found something worth checking: a mesh warning, experimental physics, or layers that don't fit. Read it and decide. Telling the AI "acknowledge it" is a real decision. |
| `awaiting_review` | The mesh is built and has findings. The AI shows them and continues once you agree. |
| `numerical quality: review_required` | The solve didn't fully settle. Treat its numbers as rough. |
| `experimental_validation_pending` | Always present for now. The results are real CFD but have not been checked against wind-tunnel data. |
| `physics_maturity: experimental` | The run used an experimental feature, such as a ground plane, a second rotor or transient flow. |

You can watch any run live at <http://localhost:8080>.

## 7. Stop GustSim

Press `Ctrl+C` in the terminal from step 1, or run:

```sh
docker compose stop
```

Your results stay in the Docker data volume. **Never run `docker compose down -v`**: `-v` deletes all your data.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach GustSim at http://127.0.0.1:8080" | Docker Desktop is off, or `docker compose up` isn't running. |
| The simulation stays `queued` | The worker is offline. Check that Docker Desktop is running and look at the `docker compose up` terminal for errors. |
| Claude doesn't list the gustsim tools | Check the `PYTHONPATH` path, fully restart the app, and make sure the server is switched on in the tools menu. In Claude Code, run `/mcp`. |
| `python` not found, or it opens the Microsoft Store | Install Python 3.11+ from python.org, or use the full path to `python.exe` as `command`. |
| The AI's tool call times out on long runs | Ask it to wait in shorter steps: "use wait_s 60 and check again." The run keeps going either way. |
| File not found on import | Give the AI the full absolute path to the file on this computer. |
