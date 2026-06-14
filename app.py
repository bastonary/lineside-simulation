import streamlit as st
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide")

# --- 1. INITIALIZE GLOBAL STATE ENGINE ---
if "sim_time" not in st.session_state:
    st.session_state.sim_time = 0          
    st.session_state.trip_log = []         
    st.session_state.starvation_events = {} # Track shortages individually per drop point  
    st.session_state.tugger_status = "Idle at Store"
    st.session_state.process_timer = -1   
    st.session_state.max_transit_secs = 1
    st.session_state.trip_counter = 0      
    st.session_state.running = False       
    st.session_state.tugger_pct = 0.0      
    st.session_state.on_return_lane = False 
    st.session_state.trip_start_time = 0   
    st.session_state.active_delivery_qty = 0
    st.session_state.current_target_point = None

    # NESTED SYSTEM TOPOLOGY: Station Group -> Individual Drop Points
    # Added "sequence_order" to automatically compute the 8-minute staggered delay (8 mins = 480 seconds)
    st.session_state.station_groups = {
        "Main Assembly Line": {
            "RA110": {"inventory": 23, "rop": 15, "qty_per_pkg": 10, "pkgs_per_trip": 3, "distance_pct": 35, "sequence_order": 3},
            "RA120": {"inventory": 24, "rop": 15, "qty_per_pkg": 12, "pkgs_per_trip": 4, "distance_pct": 70, "sequence_order": 2},
            "RA130": {"inventory": 25, "rop": 12, "qty_per_pkg": 8,  "pkgs_per_trip": 5, "distance_pct": 95, "sequence_order": 1}
        }
    }
    
    # Initialize individual starvation logs
    for g_name, pts in st.session_state.station_groups.items():
        for p_name in pts.keys():
            st.session_state.starvation_events[p_name] = 0

    # Seed independent rows for historical dataframes
    snap = {p: d["inventory"] for g in st.session_state.station_groups.values() for p, d in g.items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 2. SIDEBAR CONFIGURATIONS DECK ---
st.sidebar.title("🎮 Factory Control Room")
app_mode = st.sidebar.selectbox("📂 Select Dashboard Page", ["🗺️ Live Simulation Map", "📊 Isolated Shortage Analytics"])

st.sidebar.header("⚙️ Master Line Rate Settings")
master_takt_mins = st.sidebar.number_input("Whole Line Master Takt (Minutes)", min_value=1.0, value=8.0, step=0.5)
master_takt_secs = int(master_takt_mins * 60)

st.sidebar.header("➕ Expand Line Drop Points")
new_point_name = st.sidebar.text_input("New Drop Point Name:", placeholder="e.g., RA140")
new_point_seq = st.sidebar.number_input("Sequence Order Position (1=Downstream, higher=Upstream)", min_value=1, value=4)

if st.sidebar.button("💾 Deploy Drop Point to Line"):
    if new_point_name:
        # Auto-compute stock count dynamically following the sequential offset pattern
        calculated_stock = 20
        all_pts = st.session_state.station_groups["Main Assembly Line"]
        if all_pts:
            last_key = list(all_pts.keys())[-1]
            calculated_stock = all_pts[last_key]["inventory"] + 1

        st.session_state.station_groups["Main Assembly Line"][new_point_name] = {
            "inventory": calculated_stock,
            "rop": 15,
            "qty_per_pkg": 10,
            "pkgs_per_trip": 3,
            "distance_pct": min(95, len(all_pts) * 25 + 20),
            "sequence_order": new_point_seq
        }
        st.session_state.starvation_events[new_point_name] = 0
        st.toast(f"Deplolyed point {new_point_name} at sequence position {new_point_seq}!", icon="✅")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📐 Fine-Tune Drop Configurations")
all_active_points = [p for g in st.session_state.station_groups.values() for p in g.keys()]
selected_point = st.sidebar.selectbox("Select Point to Modify:", all_active_points if all_active_points else ["None"])

if selected_point and selected_point != "None":
    pt_ref = st.session_state.station_groups["Main Assembly Line"][selected_point]
    st.markdown(f"**Modifying Node:** `{selected_point}`")
    pt_ref["inventory"] = st.sidebar.number_input("Live Stock Level (Units)", min_value=0, value=int(pt_ref["inventory"]))
    pt_ref["rop"] = st.sidebar.number_input("Reorder Point Threshold (ROP)", min_value=0, value=int(pt_ref["rop"]))
    pt_ref["sequence_order"] = st.sidebar.number_input("Sequence Stagger Index", min_value=1, value=int(pt_ref["sequence_order"]))
    pt_ref["qty_per_pkg"] = st.sidebar.number_input("Units Per Package Box", min_value=1, value=int(pt_ref["qty_per_pkg"]))
    pt_ref["pkgs_per_trip"] = st.sidebar.number_input("Package Boxes Per Trip", min_value=1, value=int(pt_ref["pkgs_per_trip"]))
    pt_ref["distance_pct"] = st.sidebar.slider("Layout Rail Track Allocation Stop (%)", 10, 95, value=int(pt_ref["distance_pct"]))

    if st.sidebar.button("🗑️ Delete Drop Point from Line", type="primary"):
        del st.session_state.station_groups["Main Assembly Line"][selected_point]
        if selected_point in st.session_state.starvation_events:
            del st.session_state.starvation_events[selected_point]
        st.toast(f"Removed drop point {selected_point}")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚡ Simulation Physics Engine")
speed_acceleration = st.sidebar.slider("Simulation Steps Acceleration (seconds/frame)", min_value=1, max_value=60, value=15)

# Synchronize historical tracking matrix columns safely
if st.session_state.chart_data.empty or set(all_active_points) != set(st.session_state.chart_data.columns):
    snap = {p: d["inventory"] for g in st.session_state.station_groups.values() for p, d in g.items()}
    st.session_state.chart_data = pd.DataFrame([snap])

# --- 3. DYNAMIC RECALCULATION MATERIAL LOGIC ---
def advance_simulation(seconds):
    for _ in range(int(seconds)):
        st.session_state.sim_time += 1
        
        # A. Sequential Staggered Production Consumption Takt Engine
        # Base interval = Whole Line Takt (e.g., 8 mins). Downstream sequence adds staggered delays.
        for g_name, pts in st.session_state.station_groups.items():
            for p_name, data in pts.items():
                # Stagger sequence formula calculation:
                # Delays consumption based on sequence position relative to master cycle timing
                stagger_offset = (data["sequence_order"] - 1) * master_takt_secs
                target_trigger_time = st.session_state.sim_time - stagger_offset
                
                if target_trigger_time > 0 and target_trigger_time % master_takt_secs == 0:
                    if data["inventory"] > 0:
                        data["inventory"] -= 1
                    else:
                        st.session_state.starvation_events[p_name] += 1

        # B. Smart Logistics Fleet Dispatch Routine
        if st.session_state.tugger_status == "Idle at Store":
            st.session_state.tugger_pct = 0.0
            st.session_state.on_return_lane = False
            
            highest_urgency = -9999
            chosen_p = None
            chosen_g = None
            
            for g_name, pts in st.session_state.station_groups.items():
                for p_name, data in pts.items():
                    if data["inventory"] <= data["rop"]:
                        urgency = data["rop"] - data["inventory"]
                        if urgency > highest_urgency:
                            highest_urgency = urgency
                            chosen_p = p_name
                            chosen_g = g_name
            
            if chosen_p:
                st.session_state.current_target_point = (chosen_g, chosen_p)
                p_data = st.session_state.station_groups[chosen_g][chosen_p]
                st.session_state.active_delivery_qty = p_data["qty_per_pkg"] * p_data["pkgs_per_trip"]
                
                st.session_state.tugger_status = f"Loading for {chosen_p}"
                st.session_state.trip_start_time = st.session_state.sim_time
                st.session_state.process_timer = 25  
                st.session_state.max_transit_secs = 25
                
        elif st.session_state.tugger_status.startswith("Loading for"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                g, p = st.session_state.current_target_point
                st.session_state.tugger_status = f"Transit to {p}"
                st.session_state.process_timer = 50  
                st.session_state.max_transit_secs = 50
                
        elif st.session_state.tugger_status.startswith("Transit to"):
            st.session_state.process_timer -= 1
            g, p = st.session_state.current_target_point
            max_pos = st.session_state.station_groups[g][p]["distance_pct"]
            
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            st.session_state.tugger_pct = (elapsed / st.session_state.max_transit_secs) * max_pos
            
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_pct = max_pos  
                st.session_state.tugger_status = f"Unloading at {p} STOP"
                st.session_state.process_timer = 30  
                st.session_state.max_transit_secs = 30
                
        elif st.session_state.tugger_status.startswith("Unloading at"):
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                g, p = st.session_state.current_target_point
                if g in st.session_state.station_groups and p in st.session_state.station_groups[g]:
                    st.session_state.station_groups[g][p]["inventory"] += st.session_state.active_delivery_qty
                
                st.session_state.trip_counter += 1
                st.session_state.tugger_status = "Returning empty to Store"
                st.session_state.process_timer = 50
                st.session_state.max_transit_secs = 50
                st.session_state.on_return_lane = True
                
        elif st.session_state.tugger_status == "Returning empty to Store":
            st.session_state.process_timer -= 1
            g, p = st.session_state.current_target_point
            max_pos = st.session_state.station_groups[g][p]["distance_pct"]
            
            ratio = st.session_state.process_timer / st.session_state.max_transit_secs
            st.session_state.tugger_pct = ratio * max_pos
            
            if st.session_state.process_timer <= 0:
                duration = st.session_state.sim_time - st.session_state.trip_start_time
                st.session_state.trip_log.append({
                    "Trip ID": f"TRP-{st.session_state.trip_counter:03d}",
                    "Drop Point Destination": p,
                    "Payload Delivered": st.session_state.active_delivery_qty,
                    "Total Cycle Time": f"{duration}s"
                })
                st.session_state.tugger_status = "Idle at Store"
                st.session_state.current_target_point = None
                st.session_state.tugger_pct = 0.0
                st.session_state.on_return_lane = False

    # Snapshot current record levels into individual tracking lines
    snap = {p: d["inventory"] for g in st.session_state.station_groups.values() for p, d in g.items()}
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, pd.DataFrame([snap])], ignore_index=True)

# --- 4. GLOBAL SYSTEM ACTION TOOLBAR ---
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("▶️ Execute Factory Run", use_container_width=True):
        st.session_state.running = True
with c2:
    if st.button("⏸️ Halt Operations", use_container_width=True):
        st.session_state.running = False
with c3:
    if st.button("🔄 Reset Plant State", use_container_width=True):
        st.session_state.sim_time = 0
        st.session_state.trip_log = []
        st.session_state.tugger_status = "Idle at Store"
        st.session_state.current_target_point = None
        st.session_state.running = False
        st.session_state.tugger_pct = 0.0
        st.session_state.on_return_lane = False
        for p in st.session_state.starvation_events.keys():
            st.session_state.starvation_events[p] = 0
        st.rerun()

# --- 5. INTERACTIVE PAGE NAVIGATION ROUTER ---
if app_mode == "🗺️ Live Simulation Map":
    st.title("🗺️ Sequential Pipeline Layout & Visual Drop Stops")
    st.markdown(f"**Current Calibration Strategy:** Whole line processing takt cycle is set to **{master_takt_mins} minutes** ({master_takt_secs}s). Downstream points are staggered sequentially by **{master_takt_mins} minutes** intervals.")
    
    map_container_box = st.empty()
    status_msg_box = st.empty()
    kpi_metric_row = st.empty()

    def generate_html_floorplan():
        pct = st.session_state.tugger_pct
        tgt_tuple = st.session_state.current_target_point
        tgt_p = tgt_tuple[1] if tgt_tuple else None
        
        point_markers = ""
        info_cards = ""
        
        # Generate tracking blocks layout dynamically
        for g_name, pts in st.session_state.station_groups.items():
            for p_name, d in pts.items():
                pos = d["distance_pct"]
                is_targeted = "background: #c92a2a; color: white; border: 2px solid white; box-shadow: 0 0 12px #c92a2a;" if p_name == tgt_p else "background: #343a40; color: #f8f9fa;"
                
                # Plot layout drop point nodes along the transport tracks
                point_markers += f"""
                <div class="station-node-pin" style="left: {pos}%; {is_targeted}">
                    📍 {p_name} <br>
                    <span style="font-size:8px; opacity:0.85;">Seq: #{d['sequence_order']}</span>
                </div>
                """
                
                # Generate tracking data card rows
                card_style = "border-left: 5px solid #c92a2a; background-color: #fff5f5;" if p_name == tgt_p else "border-left: 5px solid #4d5154;"
                info_cards += f"""
                <div class="kpi-card-block" style="{card_style}">
                    <div style="font-weight:bold; font-size:12px; color:#212529;">Drop {p_name} (Pos #{d['sequence_order']})</div>
                    <div style="font-size:18px; font-weight:bold; color:#1a1b1c; margin:2px 0;">📦 Stock: {d['inventory']}</div>
                    <div style="font-size:10px; color:#6c757d; line-height:1.2;">
                        ROP Target: {d['rop']} u <br>
                        Pkg Size: {d['qty_per_pkg']} x {d['pkgs_per_trip']} <br>
                        <span style="color:#fa5252; font-weight:bold;">🚨 Shortage: {st.session_state.starvation_events[p_name]}s</span>
                    </div>
                </div>
                """
                
        tugger_text = f"🚜 Dispatching to {tgt_p}" if not st.session_state.on_return_lane else "🚜 Return Route"
        top_tugger_tag = f'<div class="tugger-truck" style="left: {pct}%;">{tugger_text}</div>' if not st.session_state.on_return_lane and tgt_p else ''
        bot_tugger_tag = f'<div class="tugger-truck backhaul" style="left: {pct}%;">🚜 Returning</div>' if st.session_state.on_return_lane else ''

        return f"""
        <style>
            .floorplan-wrapper {{ background: #fdfdfd; border: 1px solid #dee2e6; border-radius: 8px; padding: 20px; font-family: -apple-system, sans-serif; }}
            .flex-header-row {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 25px; }}
            .cards-container {{ display: flex; flex-wrap: wrap; gap: 12px; justify-content: flex-end; max-width: 80%; }}
            .kpi-card-block {{ background: #ffffff; border: 1px solid #e9ecef; padding: 10px 14px; border-radius: 4px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); min-width:150px; }}
            .railway-line {{ height: 18px; background: #e9ecef; border-top: 2px dashed #adb5bd; border-bottom: 2px dashed #adb5bd; position: relative; margin: 40px 0; border-radius: 4px; }}
            .backhaul-track {{ background: #f8f9fa; }}
            .depot-badge {{ position: absolute; left:-10px; top:-12px; background: #2b8a3e; color:white; padding:4px 12px; border-radius:4px; font-size:10px; font-weight:bold; z-index:15; letter-spacing:0.5px; }}
            .station-node-pin {{ position: absolute; top: -16px; padding: 4px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; transform: translateX(-50%); z-index:12; text-align:center; line-height:1.1; }}
            .tugger-truck {{ position: absolute; top: -22px; background: #1c7ed6; color: white; padding: 3px 8px; border-radius: 3px; font-size: 10px; font-weight: bold; transform: translateX(-50%); z-index: 20; white-space: nowrap; transition: left 0.05s linear; box-shadow: 0 4px 8px rgba(0,0,0,0.12); }}
            .tugger-truck.backhaul {{ background: #fa5252; }}
        </style>
        <div class="floorplan-wrapper">
            <div class="flex-header-row">
                <div style="background: #2b8a3e; color: white; padding: 15px; border-radius: 6px; font-weight: bold; min-width: 160px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                    🏪 Central Logistics Store<br><small style="font-weight:normal; opacity:0.85;">Staging Bay Terminal</small>
                </div>
                <div class="cards-container">{info_cards}</div>
            </div>
            
            <div style="position: relative; margin: 45px 25px 25px 25px;">
                <div class="railway-line">
                    <div class="depot-badge">STORE BAY</div>
                    {point_markers}
                    {top_tugger_tag}
                </div>
                <div class="railway-line backhaul-track">
                    <div class="depot-badge" style="background:#fa5252;">RETURN LANE</div>
                    {bot_tugger_tag}
                </div>
            </div>
        </div>
        """

    # --- SIMULATION AUTOMATION RENDER EXECUTION LOOP ---
    if st.session_state.running:
        while st.session_state.running:
            advance_simulation(speed_acceleration)
            map_container_box.html(generate_html_floorplan())
            status_msg_box.info(f"⚙️ **Tugger Status Fleet Controller Monitor:** `{st.session_state.tugger_status}` (Timer Step Countdown: `{st.session_state.process_timer}s`)")
            
            with kpi_metric_row.container():
                m1, m2, m3 = st.columns(3)
                m1.metric("⏱️ Total Operational Time Elapsed", f"{st.session_state.sim_time} seconds")
                m2.metric("🚜 Completed Transport Dispatches", f"{st.session_state.trip_counter} Runs")
                m3.metric("📦 Active Enroute Package Payload", f"{st.session_state.active_delivery_qty} units")
            
            time.sleep(0.04)
    else:
        map_container_box.html(generate_html_floorplan())
        status_msg_box.info(f"⏸️ **Simulation Paused:** `{st.session_state.tugger_status}`")

elif app_mode == "📊 Isolated Shortage Analytics":
    st.title("📊 Isolated Stockout & Shortage Risk Analytics Dashboard")
    st.markdown("Every drop point is plotted on its own graph below so you can check and isolate exactly where stockouts are happening.")
    
    active_cols = list(st.session_state.chart_data.columns)
    
    if not active_cols:
        st.warning("No active drop points detected on the line floor layout strategy.")
    else:
        # Build individual monitoring grids per point to clearly observe dynamic bottlenecks
        for col_name in active_cols:
            with st.container(border=True):
                c_left, c_right = st.columns([1, 4])
                with c_left:
                    st.subheader(f"📍 Station Node: {col_name}")
                    shortage_duration = st.session_state.starvation_events.get(col_name, 0)
                    st.metric("🚨 Total Shortage Accumulated", f"{shortage_duration}s", delta="Critical Risk" if shortage_duration > 0 else "Safe", delta_color="inverse")
                    
                    # Look up current live parameters
                    pt_info = st.session_state.station_groups["Main Assembly Line"].get(col_name, {})
                    if pt_info:
                        st.caption(f"**Sequence Delay:** {(pt_info['sequence_order']-1) * master_takt_secs}s Behind Start")
                        st.caption(f"**Reorder Target Point:** {pt_info['rop']} units")
                
                with c_right:
                    # Plot historical inventory vector logs separately on an independent axis view
                    st.line_chart(st.session_state.chart_data[col_name].iloc[-400:], height=180)
        
        st.markdown("---")
        st.subheader("📋 Historical Trip Logs Ledger")
        if not st.session_state.trip_log:
            st.info("No logs compiled yet. Initiate the runtime cycle to log deliveries.")
        else:
            st.dataframe(pd.DataFrame(st.session_state.trip_log), use_container_width=True)
