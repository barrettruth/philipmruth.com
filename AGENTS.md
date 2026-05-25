# AGENTS.md

## Vinyl Photo Transfer

When a normal local Wi-Fi network is available, use LocalSend as the default iPhone-to-PC transfer path.

1. On NixOS, install LocalSend with `nix profile install nixpkgs#localsend`, or run it once with `nix run nixpkgs#localsend`.
2. Launch LocalSend on Linux. If needed from a terminal, use `localsend_app`.
3. In LocalSend settings on Linux, set the receive folder to `~/Downloads/phil-vinyl` or another explicit intake directory.
4. Make sure the iPhone and laptop are on the same non-guest Wi-Fi network. Turn off VPN if device discovery fails.
5. If LocalSend cannot receive, allow TCP and UDP port `53317` through the Linux firewall.
6. Install LocalSend on the iPhone and grant Local Network and photo access.
7. In the iPhone Photos app, select the batch of photos, tap Share, tap `Options`, set format to `Current`, then share to LocalSend.
8. Choose the Linux device in LocalSend and complete the transfer.
9. Verify file count and spot-check a few files on Linux before deleting anything from the phone.
10. Keep the transferred originals untouched. Do not rename, convert, or dedupe during transfer.

## Current Intake Assumption

Assume the raw imported files live in `~/Downloads/phil-vinyl`.

Treat that directory as the immutable intake source. The next stages operate after transfer in this order:

1. sanitize
2. dedupe
3. group
4. identify
