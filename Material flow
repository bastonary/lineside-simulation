import streamlit as st
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide")

# --- 1. INITIALIZE GLOBAL STATE ---
if "sim_time" not in st.session_state:
    st.session_state.sim_time = 0          
    st.session_state.inventory = -1        
    st.session_state.chart_data = pd.DataFrame(columns=["Lineside Inventory"]) 
    st.session_state.trip_log = []         
    st.session_state.starvation_secs = 0   
    st.session_state.tugger_status = "Idle at Store"
    st.session_state.process_timer = -1   
    st.session_state.max_transit_secs = 1
    st.session_state.trip_counter = 0      
    st.session_state.running = False       
    st.session_state.tugger_pct = 0.0      
    st.session_state.on_return_lane = False 
    st.session_state.trip_start_time = 0   
    st.session_state.last_trip_duration = 0 

# --- 2. SIDEBAR CONFIGURATIONS & NAVIGATION ---
st.sidebar.title("🎮 Simulation Deck")
app_mode = st.sidebar.selectbox("📂 Select View Page", ["🗺️ Live Simulation Map", "📊 Logistics Metrics Summary"])

st.sidebar.header("🔧 Production & Layout Settings")
takt_time = st.sidebar.number_input("Takt Time (seconds/unit)", min_value=1, value=5)
init_qty = st.sidebar.number_input("Initial Lineside Stock (units)", min_value=1, value=50)
rop = st.sidebar.number_input("Reorder Point (Trigger ROP)", min_value=0, value=25)

st.sidebar.header("🚜 Logistics Physical Limits")
distance_m = st.sidebar.number_input("Distance: Store to Line (meters)", min_value=10, value=300)
speed_kmh = st.sidebar.number_input("Tugger Towing Speed (km/h)", min_value=1.0, value=6.0, step=0.5)

st.sidebar.header("📦 Packaging & Process Times")
qty_per_pkg = st.sidebar.number_input("Quantity per Package", min_value=1, value=15)
pkgs_per_trip = st.sidebar.number_input("Packages per Tugger Trip", min_value=1, value=4)
loading_min = st.sidebar.number_input("Loading Time at Store (minutes)", min_value=0.0, value=2.0, step=0.5)
unloading_min = st.sidebar.number_input("Unloading Time at Line (minutes)", min_value=0.0, value=1.5, step=0.5)

st.sidebar.header("⏳ Time Control Deck")
run_speed = st.sidebar.slider("Simulation Speed Steps (seconds/frame)", min_value=1, max_value=50, value=10)

if st.session_state.inventory == -1:
    st.session_state.inventory = init_qty
    st.session_state.chart_data = pd.DataFrame([init_qty], columns=["Lineside Inventory"])

# --- 3. STATE RECALCULATION ENGINE ---
def advance_simulation(seconds):
    speed_ms = (speed_kmh * 1000.0) / 3600.0
    calc_drive_time_secs = int(distance_m / speed_ms)
    calc_loading_secs = int(loading_min * 60)
    calc_unloading_secs = int(unloading_min * 60)
    calc_delivery_qty = int(qty_per_pkg * pkgs_per_trip)
    
    for _ in range(int(seconds)):
        st.session_state.sim_time += 1
        
        # A. Handle Takt Production Consumption
        if st.session_state.sim_time % takt_time == 0:
            if st.session_state.inventory > 0:
                st.session_state.inventory -= 1
            else:
                st.session_state.starvation_secs += 1
                
        # B. Logistics State Machine Logic
        if st.session_state.tugger_status == "Idle at Store":
            st.session_state.tugger_pct = 0.0
            st.session_state.on_return_lane = False
            if st.session_state.inventory <= rop:
                st.session_state.tugger_status = "Loading at Store"
                st.session_state.trip_start_time = st.session_state.sim_time
                st.session_state.process_timer = int(np.random.normal(calc_loading_secs, max(1, calc_loading_secs * 0.05)))
                st.session_state.max_transit_secs = max(1, st.session_state.process_timer)
                
        elif st.session_state.tugger_status == "Loading at Store":
            st.session_state.tugger_pct = 0.0
            st.session_state.on_return_lane = False
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_status = "Driving to Line"
                st.session_state.process_timer = calc_drive_time_secs
                st.session_state.max_transit_secs = max(1, calc_drive_time_secs)
                
        elif st.session_state.tugger_status == "Driving to Line":
            st.session_state.process_timer -= 1
            elapsed = st.session_state.max_transit_secs - st.session_state.process_timer
            st.session_state.tugger_pct = min(100.0, (elapsed / st.session_state.max_transit_secs) * 100.0)
            st.session_state.on_return_lane = False
            
            if st.session_state.process_timer <= 0:
                st.session_state.tugger_status = "Unloading at Line"
                st.session_state.process_timer = int(np.random.normal(calc_unloading_secs, max(1, calc_unloading_secs * 0.05)))
                st.session_state.max_transit_secs = max(1, st.session_state.process_timer)
                
        elif st.session_state.tugger_status == "Unloading at Line":
            st.session_state.tugger_pct = 100.0
            st.session_state.on_return_lane = True  
            st.session_state.process_timer -= 1
            if st.session_state.process_timer <= 0:
                st.session_state.inventory += calc_delivery_qty
                st.session_state.trip_counter += 1
                st.session_state.tugger_status = "Driving to Store (Empty)"
                st.session_state.process_timer = int(calc_drive_time_secs * 0.9) 
                st.session_state.max_transit_secs = max(1, st.session_state.process_timer)
                
        elif st.session_state.tugger_status == "Driving to Store (Empty)":
            st.session_state.process_timer -= 1
            # Inverse percentage to accurately represent driving backward visually
            st.session_state.tugger_pct = max(0.0, (st.session_state.process_timer / st.session_state.max_transit_secs) * 100.0)
            st.session_state.on_return_lane = True
            
            if st.session_state.process_timer <= 0:
                duration_secs = st.session_state.sim_time - st.session_state.trip_start_time
                st.session_state.last_trip_duration = duration_secs
                
                st.session_state.trip_log.append({
                    "Trip ID": f"TRP-{st.session_state.trip_counter:03d}",
                    "Trigger Time (Mins)": round(st.session_state.trip_start_time / 60, 2),
                    "Total Duration (Mins)": round(duration_secs / 60, 2),
                    "Delivered Vol (Pcs)": calc_delivery_qty,
                    "Supply Velocity (Pcs/Min)": round(calc_delivery_qty / (duration_secs / 60), 1)
                })
                
                st.session_state.tugger_status = "Idle at Store"
                st.session_state.process_timer = -1

    new_row = pd.DataFrame([st.session_state.inventory], columns=["Lineside Inventory"])
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, new_row], ignore_index=True)

# --- 4. GLOBAL INTERACTION DECK ---
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("▶️ Start Live Loop"):
        st.session_state.running = True
with c2:
    if st.button("⏸️ Pause Loop"):
        st.session_state.running = False
with c3:
    if st.button("🔄 Reset Plant"):
        st.session_state.sim_time = 0
        st.session_state.inventory = init_qty  
        st.session_state.chart_data = pd.DataFrame([init_qty], columns=["Lineside Inventory"])
        st.session_state.trip_log = []
        st.session_state.starvation_secs = 0
        st.session_state.tugger_status = "Idle at Store"
        st.session_state.process_timer = -1
        st.session_state.trip_counter = 0
        st.session_state.last_trip_duration = 0
        st.session_state.tugger_pct = 0.0
        st.session_state.on_return_lane = False
        st.session_state.running = False
        st.rerun()

# --- 5. PAGE DISPLAY ROUTING CONTROLLER ---
if app_mode == "🗺️ Live Simulation Map":
    
    st.markdown("""
        <style>
        .map-container { background-color: #f8f9fa; border-radius: 8px; padding: 20px; border: 1px solid #e9ecef; font-family: monospace; height: 180px; box-sizing: border-box; }
        .track-lane { height: 12px; background: repeating-linear-gradient(90deg, #dee2e6, #dee2e6 10px, #f1f3f5 10px, #f1f3f5 20px); position: relative; margin: 20px 0; border-radius: 6px; }
        .return-lane { background: repeating-linear-gradient(90deg, #ced4da, #ced4da 8px, #f8f9fa 8px, #f8f9fa 16px); }
        .terminal-node { position: absolute; top: -14px; width: 80px; text-align: center; font-weight: bold; font-size: 11px; padding: 4px; border-radius: 4px; color: white; z-index: 5; }
        .store-node { left: -20px; background-color: #2b8a3e; }
        .line-node { right: -20px; background-color: #c92a2a; }
        .tugger-icon { position: absolute; top: -14px; font-size: 22px; transition: left 0.05s linear; transform: translateX(-50%); z-index: 10; }
        </style>
    """, unsafe_allow_html=True)

    st.title("🏭 Plant Floor Material Flow & Move Simulation Map")
    
    # Pre-defined structural slots to secure layout positioning
    map_placeholder = st.empty()
    status_placeholder = st.empty()
    kpi_placeholder = st.empty()
    st.subheader("📈 Real-Time Sawtooth Inventory Timeline Profile")
    chart_placeholder = st.empty()

    def build_html_layout():
        pct = st.session_state.tugger_pct
        top_tugger = f'<div class="tugger-icon" style="left: {pct}%;">🚜</div>' if not st.session_state.on_return_lane else ''
        bot_tugger = f'<div class="tugger-icon" style="left: {pct}%;">🚜</div>' if st.session_state.on_return_lane else ''
        
        return f"""
        <div class="map-container">
            <div style="display: flex; justify-content: space-between; font-weight: bold; margin-bottom: 5px;">
                <span>🏪 Main Material Store</span>
                <span>📥 Lineside Storage Terminal ({st.session_state.inventory} units)</span>
            </div>
            <div style="position: relative; margin: 25px 40px;">
                <div class="track-lane">
                    <div class="terminal-node store-node">STORE</div>
                    {top_tugger}
                    <div class="terminal-node line-node" style="opacity:0;">LINE</div>
                </div>
                <div class="track-lane return-lane">
                    <div class="terminal-node store-node" style="opacity:0;">STORE</div>
                    {bot_tugger}
                    <div class="terminal-node line-node">LINESIDE</div>
                </div>
            </div>
        </div>
        """

    @st.fragment
    def run_smooth_loop():
        while st.session_state.running:
            advance_simulation(run_speed)
            
            # 1. Update Map Layout Content
            map_placeholder.markdown(build_html_layout(), unsafe_allow_html=True)
            
            # 2. Update Status Text
            timer_text = f"⏳ Phase Timer Remaining: {st.session_state.process_timer}s" if st.session_state.process_timer > 0 else ""
            status_placeholder.info(f"**Current Dispatch Step:** `{st.session_state.tugger_status}` &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {timer_text}")
            
            # 3. Update Metric Dashboards
            with kpi_placeholder.container():
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("⏱️ Shift Time Elapsed", f"{st.session_state.sim_time // 60}m {st.session_state.sim_time % 60}s")
                m2.metric("📦 Lineside Stock Balance", f"{st.session_state.inventory} units")
                m3.metric("🚨 Total Line Starvation", f"{round(st.session_state.starvation_secs / 60, 2)} mins")
                m4.metric("🚜 Completed Delivery Runs", f"{st.session_state.trip_counter} Trips")
                trip_t = f"{st.session_state.last_trip_duration // 60}m {st.session_state.last_trip_duration % 60}s" if st.session_state.last_trip_duration > 0 else "Calculating..."
                m5.metric("⏳ Last Cycle Trip Time", trip_t)

            # 4. Stream Timeline Chart Content 
            chart_placeholder.line_chart(st.session_state.chart_data.iloc[-600:], height=220)
            time.sleep(0.04)

        # Fallback static rendering states when simulation is paused
        map_placeholder.markdown(build_html_layout(), unsafe_allow_html=True)
        timer_text = f"⏳ Phase Timer Remaining: {st.session_state.process_timer}s" if st.session_state.process_timer > 0 else ""
        status_placeholder.info(f"**Current Dispatch Step:** `{st.session_state.tugger_status}` &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {timer_text}")
        chart_placeholder.line_chart(st.session_state.chart_data.iloc[-600:], height=220)

    run_smooth_loop()

elif app_mode == "📊 Logistics Metrics Summary":
    st.title("📊 Tugger Delivery Cycles & Run Summaries")
    st.markdown("This dashboard page compiles the tracking data from all completed replenishment circuit runs recorded during this run.")
    
    if len(st.session_state.trip_log) == 0:
        st.warning("⚠️ No completed trips recorded yet. Please run the Live Simulation loop first until deliveries are completed.")
    else:
        df_trips = pd.DataFrame(st.session_state.trip_log)
        
        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("🏁 Total Runs Logged", f"{len(df_trips)} Trips")
        kpi2.metric("⏱️ Average Cycle Duration", f"{round(df_trips['Total Duration (Mins)'].mean(), 2)} mins")
        kpi3.metric("📦 Accumulated Delivered Volume", f"{int(df_trips['Delivered Vol (Pcs)'].sum())} units")
        
        st.subheader("📋 Delivery Ledger Historical Log")
        st.dataframe(df_trips, use_container_width=True, hide_index=True)
