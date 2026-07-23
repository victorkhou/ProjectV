/*
 * Custom output plugin — replaces default_out.js
 *
 * Routes messages based on CSS class:
 *   - "game-chat" → #chatwindow (chat panel)
 *   - "ascii-map" → #messagewindow (hidden by CSS when graphical map active)
 *   - everything else → #messagewindow with a separator line
 */
let custom_out_plugin = (function () {

    var onText = function (args, kwargs) {
        var cls = (kwargs && kwargs.cls) ? kwargs.cls : "out";
        var html = args[0] || "";

        if (cls === "prompt-line") {
            // The status line (HP/level/position/terrain) is printed as normal
            // text so raw telnet clients always see it. The webclient already
            // shows the same fields in the map footer (via prompt_status), so
            // drop it here to avoid duplicating it in the output panel.
            return true;
        }

        if (cls === "game-chat") {
            // Route to chat panel with timestamp
            var cw = document.getElementById("chatwindow");
            if (cw) {
                var now = new Date();
                var ts = ("0"+now.getHours()).slice(-2)+":"+("0"+now.getMinutes()).slice(-2);
                var div = document.createElement("div");
                div.className = "out game-chat";
                div.innerHTML = "<span class='chat-ts'>[" + ts + "]</span> " + html;
                cw.appendChild(div);
                // Auto-scroll chat
                var cs = document.getElementById("chat-scroll");
                if (cs) cs.scrollTop = cs.scrollHeight;
            }
            return true;  // claim it — don't let other plugins handle it
        }

        // Everything else goes to #messagewindow
        var mwin = document.getElementById("messagewindow");
        if (mwin) {
            // Add a separator before each new server message (except ascii-map)
            if (cls !== "ascii-map" && mwin.children.length > 0) {
                var sep = document.createElement("div");
                sep.className = "msg-separator";
                mwin.appendChild(sep);
            }
            var div = document.createElement("div");
            div.className = cls;
            div.innerHTML = html;
            mwin.appendChild(div);
            // Scroll the output panel
            var outer = document.getElementById("text-scroll-outer");
            if (outer) outer.scrollTop = outer.scrollHeight;
        }
        return true;
    };

    var onPrompt = function (args, kwargs) {
        // The status prompt is shown in the map footer (see map_renderer's
        // prompt_status handler), so the dedicated .prompt bar is redundant here
        // and would duplicate/overlap the same info. Claim the event and drop it
        // so nothing renders in the bar (it's also hidden via CSS).
        return true;
    };

    var onUnknownCmd = function (cmdname, args, kwargs) {
        // Silently ignore OOB messages handled by the map_renderer plugin.
        if (cmdname === "map_update") return true;
        if (cmdname === "prompt_status") return true;

        var mwin = document.getElementById("messagewindow");
        if (mwin) {
            mwin.innerHTML += "<div class='msg err'>Unhandled: " + cmdname + "</div>";
            var outer = document.getElementById("text-scroll-outer");
            if (outer) outer.scrollTop = outer.scrollHeight;
        }
        return true;
    };

    var init = function () {
        console.log("Custom output plugin initialized.");
    };

    return {
        init: init,
        onText: onText,
        onPrompt: onPrompt,
        onUnknownCmd: onUnknownCmd,
    };
})();

plugin_handler.add("custom_out", custom_out_plugin);
