import streamlit as st
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide")

# --- 1. CLOCK FORMATTING HELPER ---
def format_to_mmss(total_seconds):
    minutes = int(total_seconds) // 60
    seconds = int(total_seconds) % 60
    return f"{minutes:02d}:{seconds:02d}"

# --- 2. INITIALIZE ARTIFACT GLOBAL STATE WITH USER DEFAULT STATIONS ---
if "sim_time" not in st.session_state:
    st.session_state.sim_time = 0          
    st.session_state.trip_log = []         
    st.session_state.starvation_events = {}  
    st.session_state.tugger_status = "Idle at Store"
    st.session_state.process_timer = -1   
    st.session_state.max_transit_secs = 1
    st.session_state.trip_counter = 0      
    st.session_state.running = False       
    st.session_state.tugger_pct = 0.0      
    st.session_state.trip_start_time = 0   
    st.session_state.active_delivery_qty = 0
    st.session_state.current_target_point = None

    # SPECIFIED USER DEFAULT TOPOLOGY: Workstation -> Point A Sub-drop point mapping
    # Sequenced backward by default so upstream feeds downstream consecutively
    st.session_state.workstations = {
        "RC100": {"sequence_order": 8, "sub_stations": {"Point A": {"inventory": 25, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 12}}},
        "RA110": {"sequence_order": 7, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 24}}},
        "RA120": {"sequence_order": 6, "sub_stations": {"Point A": {"inventory": 23, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 3, "distance_pct": 36}}},
        "RA130": {"sequence_order": 5, "sub_stations": {"Point A": {"inventory": 25, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 4, "distance_pct": 48}}},
        "RA140": {"sequence_order": 4, "sub_stations": {"Point A": {"inventory": 22, "rop": 10, "qty_per_pkg": 8,  "pkgs_per_trip": 4, "distance_pct": 60}}},
        "RA150": {"sequence_order": 3, "sub_stations": {"Point A": {"inventory": 24, "rop": 12, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 72}}},
        "RA160": {"sequence_order": 2, "sub_stations": {"Point A": {"inventory": 26, "rop": 12, "qty_per_pkg": 12, "pkgs_per_trip": 3, "distance_pct": 84}}},
        "RA170": {"sequence_order": 1, "sub_stations": {"Point A": {"inventory": 25, "rop": 15, "qty_per_pkg": 10, "pkgs_per_trip": 4, "distance_pct": 94}}}
    }
    
    # Register default tracking paths for shortage metrics
    for ws_name, ws_data in st.session_state.workstations.items():
        for sub_name in ws_data["sub_stations"].keys():
            unique_key = f"{ws_name}_{sub_name}"
            st.session_state.starvation_events[unique_key] = 0

    snap = {f"{ws}_{sub}": d["inventory"] for ws in st.session_state.workstations.values() for sub, d in ws["sub_stations"].items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 3. SIDEBAR COMPONENT INGESTION DECK ---
st.sidebar.title("🎮 Factory Control Room")
app_mode = st.sidebar.selectbox("📂 Select Dashboard Page", ["🗺️ Live Simulation Map", "📊 Isolated Shortage Analytics"])

st.sidebar.header("⚙️ Master Line Rate Settings")
master_takt_mins = st.sidebar.number_input("Whole Line Master Takt (Minutes)", min_value=1.0, value=8.0, step=0.5)
master_takt_secs = int(master_takt_mins * 60)

st.sidebar.header("🏢 Ingest / Modify Station Hierarchy")
ws_mode = st.sidebar.radio("Input Action Category:", ["Add/Update Main Workstation", "Add Sub-Station (Drop Point)"])

if ws_mode == "Add/Update Main Workstation":
    st.sidebar.markdown("### 🛠️ Define Parent Workstation")
    ws_input = st.sidebar.text_input("Workstation Name ID:", placeholder="e.g., WS-NEW")
    ws_seq = st.sidebar.number_input("Workstation Sequence Line Position:", min_value=1, value=9)
    
    if st.sidebar.button("💾 Apply Workstation Sequence Config"):
        if ws_input:
            if ws_input not in st.session_state.workstations:
                st.session_state.workstations[ws_input] = {"sequence_order": ws_seq, "sub_stations": {}}
            else:
                st.session_state.workstations[ws_input]["sequence_order"] = ws_seq
            st.toast(f"Workstation {ws_input} assigned sequence line rank #{ws_seq}!", icon="🏭")
            st.rerun()

elif ws_mode == "Add Sub-Station (Drop Point)":
    st.sidebar.markdown("### 📦 Define Child Sub-Station Drop")
    parent_ws = st.sidebar.selectbox("Select Parent Workstation Mapping:", list(st.session_state.workstations.keys()))
    new_sub_name = st.sidebar.text_input("Sub-Station (Drop Point) Code Name:", value="Point A")
    
    if st.sidebar.button("💾 Deploy Drop Sub-Station to Parent"):
        if new_sub_name and parent_ws:
            st.session_state.workstations[parent_ws]["sub_stations"][new_sub_name] = {
                "inventory": 20,
                "rop": 12,
                "qty_per_pkg": 10,
                "pkgs_per_trip": 3,
                "distance_pct": min(95, len(st.session_state.workstations[parent_ws]["sub_stations"]) * 10 + 20)
            }
            st.session_state.starvation_events[f"{parent_ws}_{new_sub_name}"] = 0
            st.toast(f"Linked sub-station drop {new_sub_name} into parent frame {parent_ws}!", icon="✅")
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📐 Fine-Tune Specific Sub-Workstations")
flat_subs = []
for ws_k, ws_v in st.session_state.workstations.items():
    for sub_k in ws_v["sub_stations"].keys():
        flat_subs.append(f"{ws_k} -> {sub_k}")

selected_flat = st.sidebar.selectbox("Select Sub-Station Node to Modify:", flat_subs if flat_subs else ["None"])

if selected_flat and selected_flat != "None":
    ws_target, sub_target = selected_flat.split(" -> ")
    pt_ref = st.session_state.workstations[ws_target]["sub_stations"][sub_target]
    
    st.markdown(f"⚙️ **Editing:** `{sub_target}` (Inheriting Sequence from `{ws_target}` = Position {st.session_state.workstations[ws_target]['sequence_order']})")
    pt_ref["inventory"] = st.sidebar.number_input("Live Stock Level (Units)", min_value=0, value=int(pt_ref["inventory"]))
    pt_ref["rop"] = st.sidebar.number_input("Reorder Safety Level (ROP)", min_value=0, value=int(pt_ref["rop"]))
    pt_ref["qty_per_pkg"] = st.sidebar.number_input("Units Count Per Package Box", min_value=1, value=int(pt_ref["qty_per_pkg"]))
    pt_ref["pkgs_per_trip"] = st.sidebar.number_input("Package Box Delivery Vol per Run", min_value=1, value=int(pt_ref["pkgs_per_trip"]))
    pt_ref["distance_pct"] = st.sidebar.slider("Circuit Visual Loop Stop Position (%)", 5, 95, value=int(pt_ref["distance_pct"]))

    if st.sidebar.button("🗑️ Wipe Sub-Station Drop Node", type="primary"):
        del st.session_state.workstations[ws_target]["sub_stations"][sub_target]
        unique_key = f"{ws_target}_{sub_target}"
        if unique_key in st.session_state.starvation_events:
            del st.session_state.starvation_events[unique_key]
        st.toast(f"Removed sub-station drop node {sub_target}")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚡ Simulation Run Controls")
speed_acceleration = st.sidebar.slider("Simulation Processing Speed Steps (s)", min_value=1, max_value=60, value=15)

# Sync dynamic data dimensions inside tracking logs
all_sub_keys = [f"{ws_k}_{sub_k}" for ws_k, ws_v in st.session_state.workstations.items() for sub_k in ws_v["sub_stations"].keys()]
if st.session_state.chart_data.empty or set(all_sub_keys) != set(st.session_state.chart_data.columns):
    snap = {f"{ws_k}_{sub_k}": d["inventory"] for ws_k, ws_v in st.session_state.workstations.items() for sub_k, d in ws_v["sub_stations"].items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 4. ENGINE ADVANCEMENT PARSING PROCESS ---
def advance_simulation(seconds):
    for _ in range(int(seconds)):
        st.session_state.sim_time += 1
        
        # A. Workstation-Level Staggered Takt Processing 
        for ws_name, ws_data in st.session_state.workstations.items():
            stagger_offset = (ws_data["sequence_order"] - 1) * master_takt_secs
            target_trigger_time = st.session_state.sim_time - stagger_offset
            
            if target_trigger_time > 0 and target_trigger_time % master_takt_secs == 0:
                for sub_name, sub_data in ws_data["sub_stations"].items():
                    unique_key = f"{ws_name}_{sub_name}"
                    if sub_data["inventory"] > 0:
                        sub_data["inventory"] -= 1
                    else:
                        st.session_state.starvation_events[unique_key] += 1

        # B. Closed Circuit Round-Trip Delivery Dispatch Engine
        if st.session_state.tugger_status == "Idle at Store":
            st.session_state.tugger_pct = 0.0
            
            highest_urgency = -9999
            chosen_ws = None
            chosen_sub = None
            
            for ws_name, ws_data in st.session_state.workstations.items():
                for sub_name, sub_data in ws_data["sub_stations"].items():
                    if sub_data["inventory"] <= sub_data["rop"]:
                        urgency = sub_data["rop"] - sub_data["inventory"]
                        if urgency > highest_urgency:
                            highest_urgency = urgency
                            chosen_sub = sub_name
                            chosen_ws = ws_name
            
            if chosen_sub:
                st.session_state.current_target_point = (chosen_ws, chosen_sub)
                p_data = st.session_state.workstations[chosen_ws]["sub_stations"][chosen_sub]
                st.session_state.active_delivery_qty = p_data["qty_per_pkg"] * p_data["pkgs_per_trip"]
                
                st.session_state.tugger_status = f"Loading for {chosen_ws}-{chosen_sub}"
                st.session_state.trip_start_time = st.session_state.sim_time
                st.session_state.process_timer = 20  
                st.session_state.max_transit_secs = 20
                
        elif st.session_state.tugger_status.startswith("Loading for"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                w, s = st.session_state.current_target_point
                st.session_state.tugger_status = f"Transit to {w}-{s}"
                st.session_state.process_timer = 45  
                st.session_state.max_transit_secs = 45
                
        elif st.session_state.tugger_status.startswith("Transit to"):
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            max_pos = st.session_state.workstations[w]["sub_stations"][s]["distance_pct"]
            
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            st.session_state.tugger_pct = (elapsed / st.session_state.max_transit_secs) * max_pos
            
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_pct = max_pos  
                st.session_state.tugger_status = f"Unloading at {w}-{s} STOP"
                st.session_state.process_timer = 25  
                st.session_state.max_transit_secs = 25
                
        elif st.session_state.tugger_status.startswith("Unloading at"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                w, s = st.session_state.current_target_point
                if w in st.session_state.workstations and s in st.session_state.workstations[w]["sub_stations"]:
                    st.session_state.workstations[w]["sub_stations"][s]["inventory"] += st.session_state.active_delivery_qty
                
                st.session_state.trip_counter += 1
                st.session_state.tugger_status = "Continuing Circle Loop to Store"
                st.session_state.process_timer = 45
                st.session_state.max_transit_secs = 45
                
        elif st.session_state.tugger_status == "Continuing Circle Loop to Store":
            st.session_state.process_timer -= 1
            w, s = st.session_state.current_target_point
            max_pos = st.session_state.workstations[w]["sub_stations"][s]["distance_pct"]
            
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            ratio = elapsed / st.session_state.max_transit_secs
            st.session_state.tugger_pct = max_pos + (ratio * (100.0 - max_pos))
            
            if st.session_state.process_timer <= 0:
                duration_secs = st.session_state.sim_time - st.session_state.trip_start_time
                st.session_state.trip_log.append({
                    "Trip ID": f"TRP-{st.session_state.trip_counter:03d}",
                    "Target Workstation Node": w,
                    "Sub-Drop Point": s,
                    "Payload Volume Refilled": f"{st.session_state.active_delivery_qty} units",
                    "Total Route Cycle Time": format_to_mmss(duration_secs)
                })
                st.session_state.tugger_status = "Idle at Store"
                st.session_state.current_target_point = None
                st.session_state.tugger_pct = 0.0

    snap = {f"{ws_k}_{sub_k}": d["inventory"] for ws_k, ws_v in st.session_state.workstations.items() for sub_k, d in ws_v["sub_stations"].items()}
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, pd.DataFrame([snap])], ignore_index=True)

# --- 5. TOP LEVEL CONTROL TOOLBAR ---
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("▶️ Run Production Cycle", use_container_width=True):
        st.session_state.running = True
with c2:
    if st.button("⏸️ Freeze Production", use_container_width=True):
        st.session_state.running = False
with c3:
    if st.button("🔄 Full Reset Plant State", use_container_width=True):
        st.session_state.sim_time = 0
        st.session_state.trip_log = []
        st.session_state.tugger_status = "Idle at Store"
        st.session_state.current_target_point = None
        st.session_state.running = False
        st.session_state.tugger_pct = 0.0
        for p in st.session_state.starvation_events.keys():
            st.session_state.starvation_events[p] = 0
        st.rerun()

# --- 6. VISUAL APP RENDER EXECUTION INTERFACE ---
if app_mode == "🗺️ Live Simulation Map":
    st.title("🗺️ Hierarchical Factory Loop Tracking Floorplan Map")
    st.markdown(f"**Current Structural Settings:** Whole line base processing takt cycle is balanced at **{master_takt_mins} minutes** ({master_takt_secs}s). Sub-station drop zones inherit sequence offsets from their parent workstation assignment block.")
    
    map_container_box = st.empty()
    status_msg_box = st.empty()
    kpi_metric_row = st.empty()

    def generate_html_floorplan():
        pct = st.session_state.tugger_pct
        tgt_tuple = st.session_state.current_target_point
        tgt_ws = tgt_tuple[0] if tgt_tuple else None
        tgt_sub = tgt_tuple[1] if tgt_tuple else None
        
        point_markers = ""
        info_cards = ""
        
        for ws_name, ws_data in st.session_state.workstations.items():
            for sub_name, d in ws_data["sub_stations"].items():
                pos = d["distance_pct"]
                is_active_target = (ws_name == tgt_ws and sub_name == tgt_sub)
                is_targeted = "background: #c92a2a; color: white; border: 2px solid white; box-shadow: 0 0 14px #c92a2a;" if is_active_target else "background: #495057; color: #f8f9fa;"
                unique_key = f"{ws_name}_{sub_name}"
                
                point_markers += f"""
                <div class="station-node-pin" style="left: {pos}%; {is_targeted}">
                    📍 {ws_name}<br><span style="font-size:9px; font-weight:normal;">{sub_name}</span><br>
                    <span style="font-size:8px; opacity:0.85;">[Seq #{ws_data['sequence_order']}]</span>
                </div>
                """
                
                card_style = "border-left: 5px solid #c92a2a; background-color: #fff5f5;" if is_active_target else "border-left: 5px solid #1c7ed6;"
                info_cards += f"""
                <div class="kpi-card-block" style="{card_style}">
                    <div style="font-weight:bold; font-size:11px; color:#495057;">🏭 {ws_name} ➔ {sub_name}</div>
                    <div style="font-size:16px; font-weight:bold; color:#1a1b1c; margin:2px 0;">📦 Stock: {d['inventory']} u</div>
                    <div style="font-size:10px; color:#6c757d; line-height:1.2;">
                        WS Sequence: #{ws_data['sequence_order']} <br>
                        ROP Threshold: {d['rop']} u <br>
                        <span style="color:#fa5252; font-weight:bold;">🚨 Outage: {format_to_mmss(st.session_state.starvation_events[unique_key])}</span>
                    </div>
                </div>
                """
                
        is_returning = False
        if tgt_ws and tgt_sub:
            is_returning = pct > st.session_state.workstations[tgt_ws]["sub_stations"][tgt_sub]["distance_pct"]
            
        tugger_label = "🚜 Circular Return Lane" if is_returning else (f"🚜 Hauling to {tgt_ws}-{tgt_sub}" if tgt_ws else "🚜 Standby at Bay")
        color_class = "returning" if is_returning else ""
        
        top_tugger_tag = f'<div class="tugger-truck {color_class}" style="left: {pct}%;">{tugger_label}</div>' if tgt_ws or pct > 0 else ''

        return f"""
        <style>
            .floorplan-wrapper {{ background: #fafafa; border: 1px solid #dee2e6; border-radius: 12px; padding: 24px; font-family: system-ui, sans-serif; }}
            .flex-header-row {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 30px; }}
            .cards-container {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; max-width: 82%; }}
            .kpi-card-block {{ background: #ffffff; border: 1px solid #e9ecef; padding: 10px 12px; border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.04); min-width:145px; }}
            .circle-loop-track {{ height: 24px; background: linear-gradient(90deg, #e9ecef 0%, #dee2e6 50%, #e9ecef 100%); border-top: 3px dashed #adb5bd; border-bottom: 3px dashed #adb5bd; position: relative; margin: 60px 0; border-radius: 12px; }}
            .depot-start-badge {{ position: absolute; left: 0%; top: -14px; background: #2b8a3e; color: white; padding: 4px 12px; border-radius: 4px; font-size: 10px; font-weight: bold; z-index: 15; transform: translateX(-50%); }}
            .depot-end-badge {{ position: absolute; left: 100%; top: -14px; background: #2b8a3e; color: white; padding: 4px 12px; border-radius: 4px; font-size: 10px; font-weight: bold; z-index: 15; transform: translateX(-50%); }}
            .station-node-pin {{ position: absolute; top: -22px; padding: 4px 10px; border-radius: 4px; font-size: 10px; font-weight: bold; transform: translateX(-50%); z-index: 12; text-align: center; line-height: 1.2; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            .tugger-truck {{ position: absolute; top: -30px; background: #1c7ed6; color: white; padding: 4px 10px; border-radius: 4px; font-size: 10px; font-weight: bold; transform: translateX(-50%); z-index: 20; white-space: nowrap; transition: left 0.05s linear; box-shadow: 0 4px 10px rgba(0,0,0,0.15); }}
            .tugger-truck.returning {{ background: #fa5252; }}
        </style>
        <div class="floorplan-wrapper">
            <div class="flex-header-row">
                <div style="background: #2b8a3e; color: white; padding: 16px; border-radius: 8px; font-weight: bold; min-width: 170px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
                    🏪 Supermarket Depot<br><small style="font-weight:normal; opacity:0.85;">Central Staging Hub</small>
                </div>
                <div class="cards-container">{info_cards}</div>
            </div>
            
            <div style="position: relative; margin: 60px 30px 30px 30px;">
                <div class="circle-loop-track">
                    <div class="depot-start-badge">🏁 DEPART</div>
                    {point_markers}
                    {top_tugger_tag}
                    <div class="depot-end-badge">🔄 STORE</div>
                </div>
            </div>
        </div>
        """

    if st.session_state.running:
        while st.session_state.running:
            advance_simulation(speed_acceleration)
            map_container_box.html(generate_html_floorplan())
            status_msg_box.info(f"🚜 **Tugger Fleet Status Monitor:** `{st.session_state.tugger_status}` (Task Countdown Timer: `{st.session_state.process_timer}s`)")
            
            with kpi_metric_row.container():
                m1, m2, m3 = st.columns(3)
                m1.metric("⏱️ Operational Time Elapsed (MM:SS)", format_to_mmss(st.session_state.sim_time))
                m2.metric("🚜 Completed Transport Runs", f"{st.session_state.trip_counter} Cycles")
                m3.metric("📦 Active Dispatched Volume", f"{st.session_state.active_delivery_qty} units")
            
            time.sleep(0.04)
    else:
        map_container_box.html(generate_html_floorplan())
        status_msg_box.info(f"⏸️ **Simulation Paused:** `{st.session_state.tugger_status}`")

elif app_mode == "📊 Isolated Shortage Analytics":
    st.title("📊 Sub-Station Isolated Bottleneck Analysis Dashboard")
    st.markdown("Each child sub-station (drop point) is dynamically plotted below with independent telemetry lines to ensure accurate shortage diagnostics.")
    
    active_cols = list(st.session_state.chart_data.columns)
    
    if not active_cols:
        st.warning("No drop sub-station locations recorded on the track floorplan layout.")
    else:
        for col_name in active_cols:
            ws_part, sub_part = col_name.split("_")
            with st.container(border=True):
                c_left, c_right = st.columns([1, 4])
                with c_left:
                    st.subheader(f"📍 {ws_part} - {sub_part}")
                    shortage_duration_secs = st.session_state.starvation_events.get(col_name, 0)
                    st.metric(
                        "🚨 Accumulated Shortage", 
                        format_to_mmss(shortage_duration_secs), 
                        delta="Shortage Risk" if shortage_duration_secs > 0 else "Stable", 
                        delta_color="inverse"
                    )
                    
                    ws_d = st.session_state.workstations.get(ws_part, {})
                    if ws_d:
                        st.caption(f"**WS Sequence Position:** #{ws_d['sequence_order']}")
                        st.caption(f"**Stagger Offset:** {format_to_mmss((ws_d['sequence_order']-1) * master_takt_secs)}")
                        st.caption(f"**ROP Target:** {ws_d['sub_stations'][sub_part]['rop']} units")
                
                with c_right:
                    st.line_chart(st.session_state.chart_data[col_name].iloc[-400:], height=180)
        
        st.markdown("---")
        st.subheader("📋 Logistics Dispatch Historical Trip Logs Ledger")
        if not st.session_state.trip_log:
            st.info("No distribution cycles compiled into ledger yet. Initiate production workflow on the live layout page map.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.trip_log), use_container_width=True)
