/*
 * Map Renderer Plugin
 *
 * Layout: map left | chat top-right, output bottom-right
 * Resizable panels. Tab toggles map/text-only mode.
 * Routes game-chat messages to the chat panel.
 */
let map_renderer_plugin = (function () {

    const TILE_SIZE = 30;

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

    // ---- Sprite assets ----
    // 32x32 PNGs served from /static/webclient/sprites/. Each draw path tries a
    // sprite first and falls back to the original canvas-shape drawing when a
    // sprite is missing/still loading/failed — so a partial asset set (or none)
    // still renders, and a bad path never blanks the map.
    var SPRITE_BASE = "/static/webclient/sprites/";
    var SPRITES = { buildings:{}, terrain:{}, units:{}, resources:{}, ui:{} };

    function _spriteReady(img){
        return !!(img && img.complete && img.naturalWidth > 0 && !img._failed);
    }

    // Coalesce the re-render triggered as sprites finish loading: the first
    // map_update may arrive before images are ready (fallback shapes drawn),
    // then each load schedules one repaint on the next frame.
    var _rerenderQueued = false;
    function _scheduleRerender(){
        if (_rerenderQueued) return;
        _rerenderQueued = true;
        requestAnimationFrame(function(){
            _rerenderQueued = false;
            if (lastMapData && currentView === "map") renderMap(lastMapData);
        });
    }

    function _loadSprite(cat, key, file){
        var img = new Image();
        img.onload = function(){ _scheduleRerender(); };
        img.onerror = function(){ img._failed = true; };  // silent → fallback path
        img.src = SPRITE_BASE + cat + "/" + file;
        SPRITES[cat][key] = img;
    }

    // Resource-bearing terrain → resource sprite key (mirrors data/definitions/
    // terrain.yaml resource_type). Drives the corner resource badge; terrains not
    // listed here bear no resource.
    var TERRAIN_RESOURCE = {
        "Forest":"wood","Pine_Forest":"wood",
        "Rock":"stone","Permafrost":"stone","Obsidian_Plain":"stone",
        "Mountain":"iron","Scrapyard":"iron","Ice_Cave":"iron",
        "Scorched_Rock":"iron","Asteroid":"iron","Armory_Ruin":"iron",
        "Power_Grid":"energy","Magma_Vent":"energy","Generator_Room":"energy","Nebula":"energy",
        "Circuit_Field":"circuits","Control_Room":"circuits","Debris":"circuits",
        "Vault_Room":"nexium",
    };

    function loadAllSprites(){
        // Buildings — one icon per CATEGORY (4 buckets). The specific building is
        // told apart by its abbreviation, overlaid on the icon at draw time
        // (drawBuilding). Ownership is a cyan (own) / red (enemy) tint + border,
        // applied at render time, so no per-owner sprite variants are needed.
        ["headquarters","defense","technology","utility"].forEach(function(c){
            _loadSprite("buildings", c, "building_"+c+".png");
        });
        _loadSprite("buildings","occupied_overlay","building_occupied_overlay.png");
        _loadSprite("buildings","unknown","building_unknown.png");
        // Terrain — keyed by canonical type name (file is the lowercased form).
        Object.keys(TERRAIN_COLORS).forEach(function(t){
            if (t === "unknown") return;
            _loadSprite("terrain", t, "terrain_"+t.toLowerCase()+".png");
        });
        _loadSprite("terrain","out_of_bounds","terrain_out_of_bounds.png");
        // Units.
        ["player_self","player_enemy","player_linkdead"].forEach(function(k){
            _loadSprite("units", k, k+".png");
        });
        ["harvester","engineer","soldier","guard","scout","medic"].forEach(function(r){
            _loadSprite("units", r+"_own",   "unit_"+r+"_own.png");
            _loadSprite("units", r+"_enemy", "unit_"+r+"_enemy.png");
        });
        _loadSprite("units","enemy_guard","unit_enemy_guard.png");
        // Resources.
        ["wood","stone","iron","energy","circuits","nexium"].forEach(function(r){
            _loadSprite("resources", r, "resource_"+r+".png");
        });
        // UI — preloaded for future per-tile signals (selection / reticle /
        // construction). Not drawn yet: the map_update payload carries no such
        // per-tile flag, so wiring these needs a server-side data change.
        ["selection","target_reticle","construction"].forEach(function(u){
            _loadSprite("ui", u, "ui_"+u+".png");
        });
    }

    // ---- Sprite drawing helpers ----
    var TILE = TILE_SIZE;
    var HALF = TILE/2;

    // Building abbreviation → category icon bucket. There are only 4 icons; the
    // specific building is told apart by its abbreviation, overlaid on the icon.
    // Mirrors the "map by function" grouping of data/definitions/buildings.yaml
    // categories: headquarters; defense+military → defense; equipment+research+
    // intelligence → technology; resource+training+storage+medical → utility.
    var BUILDING_CATEGORY = {
        "HQ":"headquarters",
        "WL":"defense", "TU":"defense", "RL":"defense", "BK":"defense",
        "AR":"technology", "LB":"technology", "RD":"technology",
        "EX":"utility", "AC":"utility", "VT":"utility", "MB":"utility",
    };

    // Ownership tint — own = cyan, enemy = red. Applied as a translucent wash +
    // border over the shared category icon so the 4 base icons still read as
    // yours vs. theirs.
    var OWN_TINT   = "rgba(0,220,220,0.30)",  OWN_BORDER   = "#00cccc";
    var ENEMY_TINT = "rgba(220,60,60,0.32)",  ENEMY_BORDER = "#cc3333";

    // Draw the building's abbreviation centered on the icon: bold white fill with
    // a solid black outline (block outline) so it stays legible on any icon.
    function drawBuildingLabel(ctx,cx,cy,label){
        ctx.font="bold 10px sans-serif";
        ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.lineJoin="round";
        ctx.lineWidth=3;ctx.strokeStyle="#000";ctx.strokeText(label,cx,cy);
        ctx.fillStyle="#fff";ctx.fillText(label,cx,cy);
    }

    function drawBuilding(ctx,x,y,bld,state){
        var type=(bld.type||"??").substring(0,2).toUpperCase();
        var own=bld.own;
        var occupied=bld.occupied;

        // One icon per category (headquarters/defense/technology/utility); the
        // abbreviation overlaid on top differentiates buildings in the bucket.
        var cat=BUILDING_CATEGORY[type];
        var spr=cat?SPRITES.buildings[cat]:null;
        if(!_spriteReady(spr)) spr=SPRITES.buildings["unknown"];
        if(_spriteReady(spr)){
            ctx.drawImage(spr,x+2,y+2,TILE-4,TILE-4);
            // Ownership wash + border over the shared icon.
            ctx.fillStyle=own?OWN_TINT:ENEMY_TINT;ctx.fillRect(x+2,y+2,TILE-4,TILE-4);
            ctx.strokeStyle=own?OWN_BORDER:ENEMY_BORDER;ctx.lineWidth=1.5;
            ctx.strokeRect(x+2.5,y+2.5,TILE-5,TILE-5);
            if(state==="fog"){ctx.fillStyle="rgba(0,0,0,0.55)";ctx.fillRect(x+2,y+2,TILE-4,TILE-4);}
            // Abbreviation label (white text, black block outline).
            drawBuildingLabel(ctx,x+HALF,y+HALF,type);
            if(occupied){
                var ov=SPRITES.buildings["occupied_overlay"];
                if(_spriteReady(ov)){ ctx.drawImage(ov,x+2,y+2,TILE-4,TILE-4); }
                else{ ctx.strokeStyle="#4466cc";ctx.lineWidth=2;ctx.strokeRect(x+3,y+3,TILE-6,TILE-6); }
            }
            return;
        }

        // ---- Fallback: rounded rect + abbreviation (no icon sprite ready). ----
        var color=own?"#00cccc":"#cc3333";
        if(state==="fog") color=dimColor(color,0.5);
        ctx.fillStyle=color;
        roundRect(ctx,x+2,y+2,TILE-4,TILE-4,3);ctx.fill();
        ctx.strokeStyle="#fff";ctx.lineWidth=1;roundRect(ctx,x+2,y+2,TILE-4,TILE-4,3);ctx.stroke();
        if(occupied){ctx.strokeStyle="#4466cc";ctx.lineWidth=2;ctx.strokeRect(x+3,y+3,TILE-6,TILE-6);}
        drawBuildingLabel(ctx,x+HALF,y+HALF,type);
    }

    function drawAgent(ctx,x,y,ag){
        // Sprite path: <role>_<own|enemy>; an enemy with no known role uses the
        // NPC-base guard sprite.
        var role=(ag.role||"").toLowerCase();
        var spr=role?SPRITES.units[role+(ag.own?"_own":"_enemy")]:null;
        if(!_spriteReady(spr)&&!ag.own) spr=SPRITES.units["enemy_guard"];
        if(_spriteReady(spr)){ ctx.drawImage(spr,x+2,y+2,TILE-4,TILE-4); return; }

        // ---- Fallback: colored circle + role initial. ----
        var color=ag.own?"#33cc33":"#ff3333";
        var label=ag.own?(ag.role?ag.role.charAt(0).toUpperCase():"A"):"!";
        ctx.fillStyle=color;ctx.beginPath();
        ctx.arc(x+HALF,y+HALF,7,0,Math.PI*2);ctx.fill();
        ctx.strokeStyle="#fff";ctx.lineWidth=1;ctx.beginPath();
        ctx.arc(x+HALF,y+HALF,7,0,Math.PI*2);ctx.stroke();
        ctx.fillStyle="#fff";ctx.font="bold 9px sans-serif";
        ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText(label,x+HALF,y+HALF);
    }

    function drawPlayer(ctx,x,y){
        var spr=SPRITES.units["player_self"];
        if(_spriteReady(spr)){ ctx.drawImage(spr,x+1,y+1,TILE-2,TILE-2); return; }
        // ---- Fallback: gold diamond with glow. ----
        var cx=x+HALF,cy=y+HALF;
        ctx.shadowColor="#ffdd00";ctx.shadowBlur=6;
        ctx.fillStyle="#ffdd00";ctx.beginPath();
        ctx.moveTo(cx,y+2);ctx.lineTo(x+TILE-2,cy);
        ctx.lineTo(cx,y+TILE-2);ctx.lineTo(x+2,cy);ctx.closePath();ctx.fill();
        ctx.shadowBlur=0;
        ctx.strokeStyle="#000";ctx.lineWidth=1.5;ctx.beginPath();
        ctx.moveTo(cx,y+2);ctx.lineTo(x+TILE-2,cy);
        ctx.lineTo(cx,y+TILE-2);ctx.lineTo(x+2,cy);ctx.closePath();ctx.stroke();
        ctx.fillStyle="#000";ctx.font="bold 11px sans-serif";
        ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("@",cx,cy);
    }

    function drawEnemyPlayer(ctx,x,y,linkdead){
        // A linkdead (disconnected) player lingers on the tile as a target —
        // draw the greyed 'linkdead' variant instead of the live enemy.
        var spr=linkdead?SPRITES.units["player_linkdead"]:SPRITES.units["player_enemy"];
        if(_spriteReady(spr)){ ctx.drawImage(spr,x+1,y+1,TILE-2,TILE-2); return; }
        // ---- Fallback: circle + marker (grey/'z' for linkdead, red/'!' else). ----
        ctx.fillStyle=linkdead?"#777777":"#ff3333";ctx.beginPath();
        ctx.arc(x+HALF,y+HALF,8,0,Math.PI*2);ctx.fill();
        ctx.strokeStyle="#fff";ctx.lineWidth=1.5;ctx.beginPath();
        ctx.arc(x+HALF,y+HALF,8,0,Math.PI*2);ctx.stroke();
        ctx.fillStyle="#fff";ctx.font="bold 10px sans-serif";
        ctx.textAlign="center";ctx.textBaseline="middle";
        ctx.fillText(linkdead?"z":"!",x+HALF,y+HALF);
    }

    // Terrain detail overlays for resource tiles
    function drawTerrainDetail(ctx,x,y,terrain){
        // Prefer the resource sprite badge (bottom-right corner, ~11px) when the
        // terrain bears a resource and the sprite is loaded.
        var rkey=TERRAIN_RESOURCE[terrain];
        if(rkey){
            var rspr=SPRITES.resources[rkey];
            if(_spriteReady(rspr)){
                var s=11;
                ctx.drawImage(rspr,x+TILE-s-1,y+TILE-s-1,s,s);
                return;
            }
        }
        // Fallback: glyph in the corner for resource-bearing terrain.
        var icon=null,color=null;
        switch(terrain){
            case "Forest":case "Pine_Forest":icon="♣";color="#1a4a0a";break;
            case "Rock":case "Permafrost":case "Obsidian_Plain":icon="◆";color="#666";break;
            case "Mountain":case "Ice_Cave":case "Scorched_Rock":case "Asteroid":case "Armory_Ruin":icon="▲";color="#888";break;
            case "Power_Grid":case "Magma_Vent":case "Generator_Room":case "Nebula":icon="⚡";color="#cc9900";break;
            case "Circuit_Field":case "Control_Room":case "Debris":icon="◎";color="#2a8a7f";break;
            case "Vault_Room":icon="✦";color="#8844aa";break;
            default:return;
        }
        ctx.fillStyle=color;ctx.font="bold 8px sans-serif";
        ctx.textAlign="right";ctx.textBaseline="bottom";
        ctx.fillText(icon,x+TILE-2,y+TILE-1);
    }

    function roundRect(ctx,x,y,w,h,r){
        ctx.beginPath();ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);
        ctx.quadraticCurveTo(x+w,y,x+w,y+r);ctx.lineTo(x+w,y+h-r);
        ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);ctx.lineTo(x+r,y+h);
        ctx.quadraticCurveTo(x,y+h,x,y+h-r);ctx.lineTo(x,y+r);
        ctx.quadraticCurveTo(x,y,x+r,y);ctx.closePath();
    }

    // ---- Map rendering ----
    function renderMap(data) {
        if (!canvas||!ctx) return;
        if (!data||!data.tiles||!data.bounds||!data.player) return;
        lastMapData = data;
        var bounds=data.bounds, cols=bounds.max_x-bounds.min_x+1, rows=bounds.max_y-bounds.min_y+1;
        var px=data.player.x, py=data.player.y;

        // The canvas keeps a full-resolution backing buffer sized to the map,
        // but the PANEL width is governed by CSS (50%) / the user's drag — we
        // no longer stretch the panel to the raw map pixel width (which used to
        // let a wide map eat ~70% of the screen and stomp any manual resize).
        // The canvas scales down to fit via max-width/max-height in map.css.
        canvas.width = cols*TILE_SIZE;
        canvas.height = rows*TILE_SIZE;

        // Crisp pixel-art scaling — never blur the 32px sprites up/down to the
        // tile size. (Resetting canvas.width above clears this flag, so set it
        // every render.)
        ctx.imageSmoothingEnabled = false;

        ctx.fillStyle="#0a0a0a"; ctx.fillRect(0,0,canvas.width,canvas.height);

        var lookup={};
        for(var i=0;i<data.tiles.length;i++){var t=data.tiles[i]; lookup[t.x+","+t.y]=t;}

        for(var row=0;row<rows;row++){
            for(var col=0;col<cols;col++){
                var tx=bounds.min_x+col, ty=bounds.max_y-row;
                var tile=lookup[tx+","+ty], sx=col*TILE_SIZE, sy=row*TILE_SIZE;
                if(!tile){ctx.fillStyle="#050505";ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);continue;}
                // Out-of-bounds tiles (beyond the planet edge) are fog of war and
                // not real land — draw a flat grey off-map fill, not dimmed
                // terrain, so the map edge reads as "outside the world".
                if(tile.out_of_bounds){
                    var oob=SPRITES.terrain["out_of_bounds"];
                    if(_spriteReady(oob)){ ctx.drawImage(oob,sx,sy,TILE_SIZE,TILE_SIZE); }
                    else{
                        ctx.fillStyle="#1a1a1a";ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);
                        ctx.strokeStyle="rgba(0,0,0,0.25)";ctx.lineWidth=0.5;ctx.strokeRect(sx,sy,TILE_SIZE,TILE_SIZE);
                    }
                    continue;
                }
                if(tile.state==="unexplored"){
                    ctx.fillStyle="#0a0a0a";ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);
                    ctx.fillStyle="#151515";ctx.fillRect(sx+8,sy+8,3,3);
                    continue;
                }
                // Terrain base: sprite if loaded, else the flat color fill.
                var bc=getColor(tile.terrain);
                var tspr=SPRITES.terrain[tile.terrain];
                if(_spriteReady(tspr)){
                    ctx.drawImage(tspr,sx,sy,TILE_SIZE,TILE_SIZE);
                    // Fog dims the sprite via a translucent black overlay (can't
                    // recolor a bitmap like a fillStyle).
                    if(tile.state==="fog"){ctx.fillStyle="rgba(0,0,0,0.65)";ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);}
                }else{
                    ctx.fillStyle=(tile.state==="fog")?dimColor(bc,0.35):bc;
                    ctx.fillRect(sx,sy,TILE_SIZE,TILE_SIZE);
                }
                // Subtle grid lines
                ctx.strokeStyle="rgba(0,0,0,0.15)";ctx.lineWidth=0.5;ctx.strokeRect(sx,sy,TILE_SIZE,TILE_SIZE);
                // Terrain resource badge (only on visible tiles).
                if(tile.state==="visible"){drawTerrainDetail(ctx,sx,sy,tile.terrain);}
                // Building
                if(tile.building){drawBuilding(ctx,sx,sy,tile.building,tile.state);}
                // Agent marker — show as small badge in corner when on a building tile
                if(tile.agents&&tile.agents.length>0&&tile.state==="visible"){
                    var ag=tile.agents[0];
                    if(tile.building){
                        // Agent shares a building tile — small badge, top-right.
                        var arole=(ag.role||"").toLowerCase();
                        var aspr=arole?SPRITES.units[arole+(ag.own?"_own":"_enemy")]:null;
                        if(!_spriteReady(aspr)&&!ag.own) aspr=SPRITES.units["enemy_guard"];
                        if(_spriteReady(aspr)){
                            var bs=13;
                            ctx.drawImage(aspr,sx+TILE-bs-1,sy+1,bs,bs);
                        }else{
                            var agc=ag.own?"#33cc33":"#ff3333";
                            var agl=ag.own?(ag.role?ag.role.charAt(0).toUpperCase():"A"):"!";
                            ctx.fillStyle=agc;ctx.beginPath();
                            ctx.arc(sx+TILE-5,sy+5,5,0,Math.PI*2);ctx.fill();
                            ctx.fillStyle="#fff";ctx.font="bold 7px sans-serif";
                            ctx.textAlign="center";ctx.textBaseline="middle";
                            ctx.fillText(agl,sx+TILE-5,sy+5);
                        }
                    } else {
                        drawAgent(ctx,sx,sy,ag);
                    }
                }
                // Other players on the tile. Each entry is {name, linkdead}.
                // Draw the linkdead variant when EVERY player here is linkdead
                // (a live player among them means the tile is actively contested,
                // so it should read as a live threat).
                if(tile.players&&tile.players.length>0){
                    var allLinkdead=tile.players.every(function(p){
                        return p&&typeof p==="object"&&p.linkdead;
                    });
                    drawEnemyPlayer(ctx,sx,sy,allLinkdead);
                }
            }
        }
        // Player marker (always on top)
        var pcol=px-bounds.min_x, prow=bounds.max_y-py;
        var psx=pcol*TILE_SIZE, psy=prow*TILE_SIZE;
        drawPlayer(ctx,psx,psy);
        // Vision circle
        var vr=data.vision_radius;
        ctx.strokeStyle="rgba(255,255,100,0.12)";ctx.lineWidth=1;ctx.beginPath();
        ctx.arc(psx+HALF,psy+HALF,(vr+0.5)*TILE_SIZE,0,Math.PI*2);ctx.stroke();
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
            // Restore a previously-dragged split; otherwise the CSS default
            // (flex: 0 0 50%) applies. Stored as a percentage so it holds up
            // across window-size changes.
            var savedPct = null;
            try { savedPct = localStorage.getItem("mapPanelPct"); } catch (err) {}
            if (savedPct !== null) mapPanel.style.flexBasis = savedPct + "%";

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
                // Drive flex-basis (the panel is a flex item now), and store the
                // split as a percentage of the content width so it persists.
                var pct = (w / mainRect.width) * 100;
                mapPanel.style.flexBasis = pct + "%";
                try { localStorage.setItem("mapPanelPct", pct.toFixed(2)); } catch (err) {}
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

        // Move the caret to the end of the input (after a history swap).
        function caretToEnd() {
            var len = inputEl.value.length;
            try { inputEl.setSelectionRange(len, len); } catch (err) {}
        }

        function historyBack() {
            // Save the partially-typed line when navigation begins.
            if (histPos === -1) savedLine = inputEl.value;
            if (histPos < hist.length - 1) {
                histPos++;
                inputEl.value = hist[hist.length - 1 - histPos];
            }
        }

        function historyForward() {
            if (histPos > 0) {
                histPos--;
                inputEl.value = hist[hist.length - 1 - histPos];
            } else if (histPos === 0) {
                histPos = -1;
                inputEl.value = savedLine;
            }
        }

        // Bind at the DOCUMENT level (like the Tab handler) rather than on the
        // input element. default_in.js's document-level keydown focuses the
        // input on the FIRST keystroke, but because it fires on `document`
        // (not on #inputfield) an input-bound listener would miss that first
        // press — so the old code needed a second Up press to cycle. Handling
        // it at the document level lets a single press both focus the input
        // and navigate history. Only act when the map (canvas) view is not the
        // one swallowing input, and never hijack modified arrows.
        document.addEventListener("keydown", function(e) {
            if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
            if (e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
            // Ignore when focus is on another editable field (future-proofing).
            var active = document.activeElement;
            if (active && active !== inputEl &&
                (active.tagName === "TEXTAREA" || active.tagName === "INPUT")) {
                return;
            }
            if (inputEl !== document.activeElement) {
                inputEl.focus();
            }
            if (e.key === "ArrowUp") {
                historyBack();
            } else {
                historyForward();
            }
            caretToEnd();
            e.preventDefault();
        });

        // Capture sent lines into history on Enter (input-scoped is fine here
        // because Enter-to-send only happens while the input is focused).
        inputEl.addEventListener("keydown", function(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                var val = inputEl.value.trim();
                if (val && (hist.length === 0 || hist[hist.length - 1] !== val)) {
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

        // Begin loading the 32x32 pixel-art sprites; each load re-renders the
        // last map so sprites pop in as they arrive (fallback shapes until then).
        loadAllSprites();

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
