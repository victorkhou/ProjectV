/*
 * Map Renderer Plugin
 *
 * Layout: map left | chat top-right, output bottom-right
 * Resizable panels. Tab toggles map/text-only mode.
 * Routes game-chat messages to the chat panel.
 */
let map_renderer_plugin = (function () {

    const TILE_SIZE = 20;

    const TERRAIN_COLORS = {
        // Terra (earth)
        "Plains":"#4a7c3f","Forest":"#2d5a1e","Dirt":"#8b7355",
        "Rock":"#888888","Mountain":"#aaaaaa","River":"#3388bb",
        "Sand":"#c8b060","Snow":"#ccccdd",
        // Forge (industrial)
        "Power_Grid":"#c8b400","Scrapyard":"#8b6914","Circuit_Field":"#2a9d8f",
        "Factory_Floor":"#777777","Ruins":"#555555","Toxic_Waste":"#aa3333",
        "Pipeline":"#888888","Warehouse":"#888888",
        // Tundra (frozen)
        "Snowfield":"#ccccdd","Frozen_Lake":"#88ccdd","Pine_Forest":"#2d5a1e",
        "Ice_Cave":"#77bbcc","Permafrost":"#999999","Glacier":"#bbccdd",
        "Hot_Spring":"#ccaa33","Tundra_Moss":"#5a8c4f",
        // Inferno (volcanic)
        "Ash_Wastes":"#555555","Lava_Flow":"#cc3300","Obsidian_Plain":"#444444",
        "Magma_Vent":"#ff4400","Scorched_Rock":"#8b6914","Sulfur_Pit":"#ccaa00",
        "Ember_Field":"#993300","Basalt_Ridge":"#777777",
        // Citadel (fortress)
        "Corridor":"#777777","Vault_Room":"#8844aa","Armory_Ruin":"#8b6914",
        "Control_Room":"#2a9d8f","Open_Chamber":"#999999","Blast_Door":"#aaaaaa",
        "Generator_Room":"#c8b400","Barracks_Ruin":"#555555",
        // Space
        "Void":"#0a0a1a","Nebula":"#6a2c8a","Asteroid":"#777777",
        "Debris":"#8b6914","Ice_Field":"#88ccdd","Wormhole":"#8844aa",
        "Radiation_Zone":"#aa3333","Derelict_Ship":"#555555",
        "unknown":"#333333",
    };
    const TERRAIN_SYMBOLS = {
        // Terra
        "Plains":"..","Forest":"&&","Dirt":"~~",
        "Rock":"##","Mountain":"/\\","River":"==","Sand":"::","Snow":"**",
        // Forge
        "Power_Grid":"++","Scrapyard":"%%","Circuit_Field":"::",
        "Factory_Floor":"==","Ruins":";;","Toxic_Waste":"!!","Pipeline":"--","Warehouse":"[]",
        // Tundra
        "Snowfield":"**","Frozen_Lake":"><","Pine_Forest":"&&","Ice_Cave":"()",
        "Permafrost":"##","Glacier":"/\\","Hot_Spring":"@@","Tundra_Moss":",,",
        // Inferno
        "Ash_Wastes":"~~","Lava_Flow":"!!","Obsidian_Plain":"##","Magma_Vent":"^^",
        "Scorched_Rock":"..","Sulfur_Pit":"%%","Ember_Field":"**","Basalt_Ridge":"/\\",
        // Citadel
        "Corridor":"..","Vault_Room":"[]","Armory_Ruin":"{}","Control_Room":"<>",
        "Open_Chamber":"  ","Blast_Door":"||","Generator_Room":"++","Barracks_Ruin":"==",
        // Space
        "Void":"  ","Nebula":"**","Asteroid":"<>","Debris":"%%",
        "Ice_Field":"><","Wormhole":"@@","Radiation_Zone":"!!","Derelict_Ship":"[]",
    };

    let canvas = null, ctx = null, lastMapData = null;
    let currentView = "map";

    // ---- View toggle ----
    function switchView(view) {
        currentView = view;
        var w = document.getElementById("clientwrapper");
        var btnM = document.getElementById("btn-map-view");
        var btnT = document.getElementById("btn-text-view");
        if (!w) return;
        if (view === "text") {
            w.classList.add("text-mode");
            if (btnM) btnM.classList.remove("active");
            if (btnT) btnT.classList.add("active");
            scrollToBottom("text-scroll-outer");
        } else {
            w.classList.remove("text-mode");
            if (btnM) btnM.classList.add("active");
            if (btnT) btnT.classList.remove("active");
            if (lastMapData) renderMap(lastMapData);
        }
    }

    function scrollToBottom(id) {
        var el = document.getElementById(id);
        if (el) requestAnimationFrame(function(){ el.scrollTop = el.scrollHeight; });
    }

    function setupToggle() {
        var btnM = document.getElementById("btn-map-view");
        var btnT = document.getElementById("btn-text-view");
        if (btnM) btnM.addEventListener("click", function(){ switchView("map"); });
        if (btnT) btnT.addEventListener("click", function(){ switchView("text"); });
    }

    // ---- Helpers ----
    function getColor(t){ return TERRAIN_COLORS[t]||TERRAIN_COLORS["unknown"]; }
    function dimColor(hex,f){
        var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
        return "rgb("+Math.floor(r*f)+","+Math.floor(g*f)+","+Math.floor(b*f)+")";
    }

    // ---- Map rendering ----
    function renderMap(data) {
        if (!canvas||!ctx) return;
        if (!data||!data.tiles||!data.bounds||!data.player) return;
        lastMapData = data;
        var bounds=data.bounds, cols=bounds.max_x-bounds.min_x+1, rows=bounds.max_y-bounds.min_y+1;
        var px=data.player.x, py=data.player.y;

        canvas.width = cols*TILE_SIZE;
        canvas.height = rows*TILE_SIZE;
        var panel = document.getElementById("map-panel");
        if (panel) panel.style.width = (cols*TILE_SIZE)+"px";

        ctx.fillStyle="#0a0a0a"; ctx.fillRect(0,0,canvas.width,canvas.height);

        var lookup={};
        for(var i=0;i<data.tiles.length;i++){var t=data.tiles[i]; lookup[t.x+","+t.y]=t;}

        for(var row=0;row<rows;row++){
            for(var col=0;col<cols;col++){
                var tx=bounds.min_x+col, ty=bounds.max_y-row;
                var tile=lookup[tx+","+ty], sx=col*TILE_SIZE, sy=row*TILE_SIZE;
                if(!tile){ctx.fillStyle="#050505";ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);continue;}
                var bc=getColor(tile.terrain);
                if(tile.state==="visible"){ctx.fillStyle=bc;}
                else if(tile.state==="fog"){ctx.fillStyle=dimColor(bc,0.35);}
                else{ctx.fillStyle="#0a0a0a";ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);ctx.fillStyle="#151515";ctx.fillRect(sx+8,sy+8,3,3);continue;}
                ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);
                ctx.strokeStyle="rgba(0,0,0,0.2)";ctx.strokeRect(sx,sy,TILE_SIZE,TILE_SIZE);
                if(tile.state==="visible"){
                    var sym=TERRAIN_SYMBOLS[tile.terrain]||"??";
                    ctx.fillStyle="rgba(255,255,255,0.25)";ctx.font="9px monospace";
                    ctx.textAlign="center";ctx.textBaseline="middle";
                    ctx.fillText(sym,sx+TILE_SIZE/2,sy+TILE_SIZE/2);
                }
                if(tile.building){
                    var abbr=(tile.building.type||"??").substring(0,2);
                    var bldColor;
                    if(tile.state==="visible"){
                        if(tile.building.occupied){
                            bldColor="#2244aa"; // dark blue for occupied
                        } else {
                            bldColor=tile.building.own?"#00dddd":"#cc3333";
                        }
                    } else {
                        bldColor="#662222";
                    }
                    ctx.fillStyle=bldColor;
                    ctx.fillRect(sx+2,sy+2,TILE_SIZE-4,TILE_SIZE-4);
                    ctx.fillStyle="#fff";ctx.font="bold 10px monospace";
                    ctx.textAlign="center";ctx.textBaseline="middle";
                    ctx.fillText(abbr,sx+TILE_SIZE/2,sy+TILE_SIZE/2);
                }
                // Agent markers (overworld, not inside buildings)
                if(tile.agents&&tile.agents.length>0&&tile.state==="visible"){
                    var ag=tile.agents[0]; // show highest-priority agent
                    var agColor=ag.own?"#33cc33":"#ff3333";
                    var agLabel=ag.own?(ag.role?ag.role.charAt(0).toUpperCase():"A"):"!";
                    ctx.fillStyle=agColor;ctx.beginPath();
                    ctx.arc(sx+TILE_SIZE/2,sy+TILE_SIZE/2,6,0,Math.PI*2);ctx.fill();
                    ctx.fillStyle="#fff";ctx.font="bold 9px monospace";
                    ctx.textAlign="center";ctx.textBaseline="middle";
                    ctx.fillText(agLabel,sx+TILE_SIZE/2,sy+TILE_SIZE/2);
                }
                if(tile.players&&tile.players.length>0){
                    ctx.fillStyle="#ff3333";ctx.beginPath();
                    ctx.arc(sx+TILE_SIZE/2,sy+TILE_SIZE/2,6,0,Math.PI*2);ctx.fill();
                    ctx.fillStyle="#fff";ctx.font="bold 9px monospace";
                    ctx.textAlign="center";ctx.textBaseline="middle";
                    ctx.fillText("!",sx+TILE_SIZE/2,sy+TILE_SIZE/2);
                }
            }
        }
        // Player marker
        var pcol=px-bounds.min_x, prow=bounds.max_y-py;
        var psx=pcol*TILE_SIZE, psy=prow*TILE_SIZE;
        ctx.fillStyle="#ffdd00";ctx.beginPath();
        ctx.moveTo(psx+TILE_SIZE/2,psy+2);ctx.lineTo(psx+TILE_SIZE-2,psy+TILE_SIZE/2);
        ctx.lineTo(psx+TILE_SIZE/2,psy+TILE_SIZE-2);ctx.lineTo(psx+2,psy+TILE_SIZE/2);
        ctx.closePath();ctx.fill();ctx.strokeStyle="#000";ctx.lineWidth=1;ctx.stroke();
        ctx.fillStyle="#000";ctx.font="bold 12px monospace";
        ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillText("@",psx+TILE_SIZE/2,psy+TILE_SIZE/2);
        // Vision circle
        var vr=data.vision_radius;
        ctx.strokeStyle="rgba(255,255,100,0.15)";ctx.lineWidth=1;ctx.beginPath();
        ctx.arc(psx+TILE_SIZE/2,psy+TILE_SIZE/2,(vr+0.5)*TILE_SIZE,0,Math.PI*2);ctx.stroke();
        // Info
        var info=document.getElementById("map-info");
        if(info) {
            var terrainStr=data.player.terrain||"";
            if(data.player.resource) terrainStr+=" ("+data.player.resource+")";
            var parts=["("+px+", "+py+") "+(data.player.planet||"?")];
            if(terrainStr) parts.push(terrainStr);
            parts.push((data.discovered_count||0)+" discovered");
            info.textContent=parts.join(" | ");
        }
    }

    // ---- Resizable panels ----
    function setupResize() {
        // Vertical resize: between chat-panel and output-panel
        var vHandle = document.getElementById("resize-handle-v");
        var chatPanel = document.getElementById("chat-panel");
        var outputPanel = document.getElementById("output-panel");
        var rightPanel = document.getElementById("right-panel");

        if (vHandle && chatPanel && outputPanel && rightPanel) {
            var draggingV = false;
            vHandle.addEventListener("mousedown", function(e) {
                e.preventDefault();
                draggingV = true;
                vHandle.classList.add("active");
                document.body.style.cursor = "row-resize";
                document.body.style.userSelect = "none";
            });
            document.addEventListener("mousemove", function(e) {
                if (!draggingV) return;
                var rect = rightPanel.getBoundingClientRect();
                var y = e.clientY - rect.top;
                var total = rect.height;
                var pct = Math.max(10, Math.min(80, (y / total) * 100));
                chatPanel.style.flex = "0 0 " + pct + "%";
                outputPanel.style.flex = "1";
            });
            document.addEventListener("mouseup", function() {
                if (draggingV) {
                    draggingV = false;
                    vHandle.classList.remove("active");
                    document.body.style.cursor = "";
                    document.body.style.userSelect = "";
                }
            });
        }

        // Horizontal resize: between map-panel and right-panel
        var mapPanel = document.getElementById("map-panel");
        if (mapPanel && rightPanel) {
            var hHandle = document.createElement("div");
            hHandle.className = "resize-handle-h-live";
            mapPanel.parentNode.insertBefore(hHandle, rightPanel);

            var draggingH = false;
            hHandle.addEventListener("mousedown", function(e) {
                e.preventDefault();
                draggingH = true;
                hHandle.classList.add("active");
                document.body.style.cursor = "col-resize";
                document.body.style.userSelect = "none";
            });
            document.addEventListener("mousemove", function(e) {
                if (!draggingH) return;
                var mainRect = document.getElementById("main-content").getBoundingClientRect();
                var x = e.clientX - mainRect.left;
                var minW = 200, maxW = mainRect.width - 200;
                var w = Math.max(minW, Math.min(maxW, x));
                mapPanel.style.width = w + "px";
            });
            document.addEventListener("mouseup", function() {
                if (draggingH) {
                    draggingH = false;
                    hHandle.classList.remove("active");
                    document.body.style.cursor = "";
                    document.body.style.userSelect = "";
                }
            });
        }
    }

    // ---- Keyboard ----
    function setupKeyboard() {
        document.addEventListener("keydown", function(e) {
            // Tab toggles map/text view regardless of focus
            if (e.key==="Tab"){e.preventDefault();switchView(currentView==="map"?"text":"map");return;}
        });
    }

    // ---- Click-to-move ----
    function setupClickMove() {
        if (!canvas) return;
        canvas.addEventListener("click", function(e) {
            if (!lastMapData) return;
            var rect=canvas.getBoundingClientRect();
            var col=Math.floor((e.clientX-rect.left)*(canvas.width/rect.width)/TILE_SIZE);
            var row=Math.floor((e.clientY-rect.top)*(canvas.height/rect.height)/TILE_SIZE);
            var b=lastMapData.bounds;
            var dx=b.min_x+col-lastMapData.player.x, dy=b.max_y-row-lastMapData.player.y;
            if(Math.abs(dx)+Math.abs(dy)!==1) return;
            var cmd=null;
            if(dx===1)cmd="east";if(dx===-1)cmd="west";
            if(dy===1)cmd="north";if(dy===-1)cmd="south";
            if(cmd) Evennia.msg("text",[cmd],{});
        });
    }

    // ---- Init ----
    // ---- Input history (Up/Down arrows in input field) ----
    function setupInputHistory() {
        var inputEl = document.getElementById("inputfield");
        if (!inputEl) return;
        var hist = [], histPos = -1, savedLine = "";
        // Hook into send to capture history
        var origOnSend = null;
        if (typeof plugin_handler !== "undefined") {
            // Intercept lines sent to capture history
            var _origSend = Evennia.msg;
        }
        inputEl.addEventListener("keydown", function(e) {
            if (e.key === "ArrowUp") {
                // Save current line if starting navigation
                if (histPos === -1) savedLine = inputEl.value;
                if (histPos < hist.length - 1) {
                    histPos++;
                    inputEl.value = hist[hist.length - 1 - histPos];
                }
                e.preventDefault();
            } else if (e.key === "ArrowDown") {
                if (histPos > 0) {
                    histPos--;
                    inputEl.value = hist[hist.length - 1 - histPos];
                } else if (histPos === 0) {
                    histPos = -1;
                    inputEl.value = savedLine;
                }
                e.preventDefault();
            } else if (e.key === "Enter" && !e.shiftKey) {
                var val = inputEl.value.trim();
                if (val && (hist.length === 0 || hist[hist.length-1] !== val)) {
                    hist.push(val);
                    if (hist.length > 50) hist.shift();
                }
                histPos = -1;
                savedLine = "";
            }
        });
    }

    var init = function() {
        canvas = document.getElementById("map-canvas");
        if (canvas) ctx = canvas.getContext("2d");

        if (typeof Evennia !== "undefined" && Evennia.emitter) {
            Evennia.emitter.on("map_update", function(args, kwargs) {
                var data = kwargs || (args && args[0]) || null;
                if (data) renderMap(data);
            });
        }

        setupToggle();
        setupResize();
        setupKeyboard();
        setupClickMove();
        setupInputHistory();
        console.log("Map Renderer Plugin initialized.");
    };

    return { init: init };
})();

plugin_handler.add("map_renderer", map_renderer_plugin);
