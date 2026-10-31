# prusa-workbench-mcp

Ask Claude to model something (Onshape) and slice it. Claude drives the PrusaSlicer **command line** through an
MCP server — one run per bed, each bed with its own settings — writes the G-code to a job folder, and a small local
viewer app shows the result so you can check it and send it to the printer (Prusa Connect / PrusaLink).

Built for an Original Prusa MINI, but nothing ties it to one printer.

**Status:** planning. See [PLAN.md](PLAN.md).

## License

[MIT](LICENSE)
