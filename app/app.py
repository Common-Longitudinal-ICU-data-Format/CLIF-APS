"""
CLIF-APS Patient Dashboard
==========================
Streamlit application to visualize patient data from CLIF-APS pipeline.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

# =============================================================================
# Page Configuration
# =============================================================================
st.set_page_config(
    page_title="CLIF-APS Dashboard",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =============================================================================
# Custom CSS for styling
# =============================================================================
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1E3A5F;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #5A6C7D;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1rem;
        border-left: 4px solid #1E3A5F;
    }
    .stSelectbox label {
        font-weight: 600;
        color: #1E3A5F;
    }
    /* Larger tab fonts */
    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
        font-size: 1.3rem;
        font-weight: 600;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
    }
    /* Larger fonts for visualization tab elements */
    .stTabs [data-baseweb="tab-panel"] label {
        font-size: 1.1rem !important;
    }
    .stTabs [data-baseweb="tab-panel"] .stSelectbox label,
    .stTabs [data-baseweb="tab-panel"] .stMultiSelect label {
        font-size: 1.15rem !important;
        font-weight: 500;
    }
    .stTabs [data-baseweb="tab-panel"] h4 {
        font-size: 1.4rem !important;
    }
    .stTabs [data-baseweb="tab-panel"] h5 {
        font-size: 1.2rem !important;
    }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# Data Loading
# =============================================================================
@st.cache_data
def load_data():
    """Load all parquet files from intermediate output directory."""
    data_dir = Path(__file__).parent.parent / "output" / "intermediate"
    
    data = {}
    
    # Load static_df
    static_path = data_dir / "static_df.parquet"
    if static_path.exists():
        data['static_df'] = pd.read_parquet(static_path)
    else:
        st.error(f"Static data file not found: {static_path}")
        return None
    
    # Load wide_df
    wide_path = data_dir / "wide_df.parquet"
    if wide_path.exists():
        data['wide_df'] = pd.read_parquet(wide_path)
    else:
        st.error(f"Wide data file not found: {wide_path}")
        return None
    
    # Load microbiology data (optional)
    micro_culture_path = data_dir / "micro_culture_df.parquet"
    if micro_culture_path.exists():
        data['micro_culture_df'] = pd.read_parquet(micro_culture_path)
    
    micro_nonculture_path = data_dir / "micro_nonculture_df.parquet"
    if micro_nonculture_path.exists():
        data['micro_nonculture_df'] = pd.read_parquet(micro_nonculture_path)
    
    return data

# =============================================================================
# Main App
# =============================================================================
def main():
    # Load data
    data = load_data()
    
    if data is None:
        st.stop()
    
    static_df = data['static_df']
    wide_df = data['wide_df']
    
    # Get list of patient IDs
    patient_ids = static_df['patient_id'].unique().tolist()
    
    # ==========================================================================
    # Sidebar - Patient Selection & Static Info
    # ==========================================================================
    with st.sidebar:
        st.markdown("## CLIF-APS")
        st.markdown("---")
        
        # Patient selector
        st.markdown("### Select Patient")
        selected_patient = st.selectbox(
            "Patient ID",
            options=patient_ids,
            index=0,
            label_visibility="collapsed"
        )
        
        # Get all hospitalizations for this patient
        patient_hospitalizations = static_df[static_df['patient_id'] == selected_patient]
        hosp_ids = patient_hospitalizations['hospitalization_id'].unique().tolist()
        
        # If multiple hospitalizations, show selector
        if len(hosp_ids) > 1:
            st.markdown("---")
            st.markdown(f"### Hospitalizations ({len(hosp_ids)})")
            selected_hosp = st.selectbox(
                "Select Hospitalization",
                options=hosp_ids,
                index=0,
                label_visibility="collapsed"
            )
            patient_static = patient_hospitalizations[patient_hospitalizations['hospitalization_id'] == selected_hosp].iloc[0]
        else:
            selected_hosp = hosp_ids[0] if hosp_ids else None
            patient_static = patient_hospitalizations.iloc[0]
        
        st.markdown("---")
        
        # Display static info for selected patient
        st.markdown("### Patient Information")
        
        # Demographics
        st.markdown("#### Demographics")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Age at Admission", f"{patient_static.get('age_at_admission', 'N/A')}")
        with col2:
            st.metric("Sex", f"{patient_static.get('sex_category', 'N/A')}")
        
        st.markdown(f"**Race:** {patient_static.get('race_category', 'N/A')}")
        st.markdown(f"**Ethnicity:** {patient_static.get('ethnicity_category', 'N/A')}")
        st.markdown(f"**Language:** {patient_static.get('language_category', 'N/A')}")
        st.markdown(f"**Discharge Category:** {patient_static.get('discharge_category', 'N/A')}")
        
        st.markdown("---")
        
        # Hospitalization Info
        st.markdown("#### Hospitalization")
        st.markdown(f"**Hospitalization ID:** {patient_static.get('hospitalization_id', 'N/A')}")
        st.markdown(f"**Admission:** {patient_static.get('admission_dttm', 'N/A')}")
        st.markdown(f"**Discharge:** {patient_static.get('discharge_dttm', 'N/A')}")
        st.markdown(f"**Admission Type:** {patient_static.get('admission_type_category', 'N/A')}")
        
        st.markdown("---")
        
        # Trial Info
        st.markdown("#### Trial Information")
        st.markdown(f"**Trial ID:** {patient_static.get('trial_id', 'N/A')}")
        st.markdown(f"**Trial Name:** {patient_static.get('trial_name', 'N/A')}")
        st.markdown(f"**Arm ID:** {patient_static.get('arm_id', 'N/A')}")
        st.markdown(f"**Enrollment:** {patient_static.get('enrollment_dttm', 'N/A')}")
        st.markdown(f"**Withdrawal:** {patient_static.get('withdrawal_dttm', 'N/A')}")
    
    # ==========================================================================
    # Main Content Area
    # ==========================================================================
    
    # Centered Logo
    logo_path = Path(__file__).parent.parent / "images" / "logo.png"
    if logo_path.exists():
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image(str(logo_path), use_container_width=True)
    
    st.markdown("---")
    
    # Filter wide_df for selected patient and hospitalization
    if selected_hosp:
        patient_wide = wide_df[
            (wide_df['patient_id'] == selected_patient) & 
            (wide_df['hospitalization_id'] == selected_hosp)
        ].copy()
    else:
        patient_wide = wide_df[wide_df['patient_id'] == selected_patient].copy()
    
    # Summary metrics
    hosp_label = f" (Hosp: {selected_hosp})" if len(hosp_ids) > 1 else ""
    st.markdown(f"### Patient {selected_patient}{hosp_label}")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Events", len(patient_wide))
    with col2:
        day_range = f"{patient_wide['day'].min()} to {patient_wide['day'].max()}" if len(patient_wide) > 0 else "N/A"
        st.metric("Day Range", day_range)
    with col3:
        n_vitals = patient_wide[[c for c in patient_wide.columns if c.startswith('vital_')]].notna().any(axis=1).sum()
        st.metric("Vital Records", n_vitals)
    with col4:
        n_labs = patient_wide[[c for c in patient_wide.columns if c.startswith('lab_')]].notna().any(axis=1).sum()
        st.metric("Lab Records", n_labs)
    
    st.markdown("---")
    
    # Create tabs
    tab_data, tab_viz = st.tabs(["📊 Event Data Preview", "📈 Visualizations"])
    
    # ==========================================================================
    # Tab 1: Event Data Preview
    # ==========================================================================
    with tab_data:
        # Sort by event_dttm
        patient_wide_sorted = patient_wide.sort_values('event_dttm', ascending=True)
        
        # Show first 50 rows
        st.dataframe(
            patient_wide_sorted.head(50),
            use_container_width=True,
            height=400
        )
        
        # Data summary
        st.markdown("### Available Columns by Category")
        categories = {
            'Vitals': [c for c in patient_wide.columns if c.startswith('vital_')],
            'Labs': [c for c in patient_wide.columns if c.startswith('lab_')],
            'Continuous Meds': [c for c in patient_wide.columns if c.startswith('med_cont_')],
            'Intermittent Meds': [c for c in patient_wide.columns if c.startswith('med_int_')],
            'Respiratory': [c for c in patient_wide.columns if c.startswith('resp_')],
            'ADT': [c for c in patient_wide.columns if c.startswith('adt_')]
        }
        
        for cat_name, cols in categories.items():
            with st.expander(f"{cat_name} ({len(cols)} columns)"):
                st.write(cols)
    
    # ==========================================================================
    # Tab 2: Visualizations
    # ==========================================================================
    with tab_viz:
        # st.markdown("### Patient Event Timeline")
        
        if len(patient_wide) == 0:
            st.warning("No event data available for this patient.")
        else:
            # Prepare timeline data - identify which event types occurred at each timestamp
            timeline_data = []
            
            # Define event categories and their colors
            event_categories = {
                'Vitals': {'prefix': 'vital_', 'color': '#2ecc71', 'symbol': 'circle'},
                'Labs': {'prefix': 'lab_', 'color': '#3498db', 'symbol': 'diamond'},
                'Continuous Meds': {'prefix': 'med_cont_', 'color': '#e74c3c', 'symbol': 'square'},
                'Intermittent Meds': {'prefix': 'med_int_', 'color': '#9b59b6', 'symbol': 'triangle-up'},
                'Respiratory': {'prefix': 'resp_', 'color': '#f39c12', 'symbol': 'star'},
                'ADT': {'prefix': 'adt_', 'color': '#1abc9c', 'symbol': 'hexagon'}
            }
            
            # Build timeline events
            for _, row in patient_wide.iterrows():
                event_time = row['event_dttm']
                day = row['day']
                
                for cat_name, cat_info in event_categories.items():
                    # Get columns for this category
                    cat_cols = [c for c in patient_wide.columns if c.startswith(cat_info['prefix'])]
                    
                    # Check if any value exists for this category at this time
                    if cat_cols:
                        has_data = row[cat_cols].notna().any()
                        if has_data:
                            # Get the non-null values for hover info
                            non_null_cols = [c for c in cat_cols if pd.notna(row[c])]
                            hover_details = "<br>".join([f"{c.replace(cat_info['prefix'], '')}: {row[c]}" 
                                                        for c in non_null_cols[:5]])  # Limit to 5 for readability
                            if len(non_null_cols) > 5:
                                hover_details += f"<br>... and {len(non_null_cols) - 5} more"
                            
                            timeline_data.append({
                                'event_dttm': event_time,
                                'day': day,
                                'category': cat_name,
                                'color': cat_info['color'],
                                'symbol': cat_info['symbol'],
                                'hover_text': f"<b>{cat_name}</b><br>Day: {day}<br>Time: {event_time}<br><br>{hover_details}"
                            })
            
            if timeline_data:
                timeline_df = pd.DataFrame(timeline_data)
                
                # Create the interactive timeline plot
                fig = go.Figure()
                
                # Add traces for each category
                for cat_name, cat_info in event_categories.items():
                    cat_data = timeline_df[timeline_df['category'] == cat_name]
                    if len(cat_data) > 0:
                        fig.add_trace(go.Scatter(
                            x=cat_data['event_dttm'],
                            y=cat_data['category'],
                            mode='markers',
                            name=cat_name,
                            marker=dict(
                                size=12,
                                color=cat_info['color'],
                                symbol=cat_info['symbol'],
                                line=dict(width=1, color='white')
                            ),
                            hovertemplate=cat_data['hover_text'].tolist(),
                            hoverlabel=dict(bgcolor=cat_info['color'])
                        ))
                
                # Add vertical line for Day 0 (enrollment date)
                if 'enrollment_dttm' in patient_wide.columns and patient_wide['enrollment_dttm'].notna().any():
                    enrollment_date = patient_wide['enrollment_dttm'].dropna().iloc[0]
                    # Convert to string for plotly compatibility
                    enrollment_str = str(enrollment_date)
                    
                    # Add vertical line shape
                    fig.add_shape(
                        type="line",
                        x0=enrollment_str, x1=enrollment_str,
                        y0=0, y1=1,
                        yref="paper",
                        line=dict(color='#e74c3c', width=3, dash='dash')
                    )
                    
                    # Add annotation separately
                    fig.add_annotation(
                        x=enrollment_str,
                        y=1.05,
                        yref="paper",
                        text="<b>Day 0 (Enrollment)</b>",
                        showarrow=False,
                        font=dict(size=12, color='#e74c3c'),
                        bgcolor="rgba(255,255,255,0.8)",
                        bordercolor="#e74c3c",
                        borderwidth=1,
                        borderpad=4
                    )
                
                # Update layout
                fig.update_layout(
                    title=dict(
                        text=f"Event Timeline for Patient {selected_patient}",
                        font=dict(size=22)
                    ),
                    xaxis=dict(
                        title=dict(text="Time", font=dict(size=16)),
                        showgrid=True,
                        gridwidth=1,
                        gridcolor='rgba(128,128,128,0.2)',
                        tickformat='%Y-%m-%d %H:%M',
                        tickfont=dict(size=13),
                        rangeslider=dict(visible=True),  # Add range slider for zooming
                    ),
                    yaxis=dict(
                        title=dict(text="Event Category", font=dict(size=16)),
                        showgrid=True,
                        gridwidth=1,
                        gridcolor='rgba(128,128,128,0.2)',
                        tickfont=dict(size=14),
                        categoryorder='array',
                        categoryarray=list(event_categories.keys())[::-1]  # Reverse order for better display
                    ),
                    height=500,
                    showlegend=True,
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="right",
                        x=1,
                        font=dict(size=14)
                    ),
                    hovermode='closest',
                    plot_bgcolor='white',
                    hoverlabel=dict(font_size=14)
                )
                
                # Display the plot
                st.plotly_chart(fig, use_container_width=True)
                
                # Add summary below the chart
                st.markdown("---")
                st.markdown("#### Event Summary")
                summary_cols = st.columns(len(event_categories))
                for i, (cat_name, cat_info) in enumerate(event_categories.items()):
                    count = len(timeline_df[timeline_df['category'] == cat_name])
                    with summary_cols[i]:
                        st.markdown(f"<div style='text-align: center; padding: 12px; background-color: {cat_info['color']}20; border-radius: 8px; border-left: 4px solid {cat_info['color']};'>"
                                    f"<span style='font-size: 1.8rem; font-weight: bold; color: {cat_info['color']};'>{count}</span><br>"
                                    f"<span style='font-size: 1.1rem; color: #666;'>{cat_name}</span></div>", 
                                    unsafe_allow_html=True)
                
                # ==========================================================
                # Category Detail Trends
                # ==========================================================
                st.markdown("---")
                st.markdown("#### Category Trend Analysis")
                
                # Category selector
                selected_category = st.selectbox(
                    "Select a category to view detailed trends:",
                    options=list(event_categories.keys()),
                    index=0
                )
                
                # Get columns for selected category
                cat_info = event_categories[selected_category]
                cat_cols = [c for c in patient_wide.columns if c.startswith(cat_info['prefix'])]
                
                if cat_cols:
                    # Filter to rows with at least one non-null value in this category
                    cat_data = patient_wide[['event_dttm', 'day'] + cat_cols].dropna(subset=cat_cols, how='all')
                    
                    if len(cat_data) > 0:
                        # Melt the data for plotting
                        cat_melted = cat_data.melt(
                            id_vars=['event_dttm', 'day'],
                            value_vars=cat_cols,
                            var_name='metric',
                            value_name='value'
                        ).dropna(subset=['value'])
                        
                        # Clean up metric names (remove prefix)
                        cat_melted['metric'] = cat_melted['metric'].str.replace(cat_info['prefix'], '', regex=False)
                        
                        if len(cat_melted) > 0:
                            # Multi-select for specific metrics
                            available_metrics = cat_melted['metric'].unique().tolist()
                            selected_metrics = st.multiselect(
                                "Select metrics to display:",
                                options=available_metrics,
                                default=available_metrics[:min(5, len(available_metrics))]  # Default to first 5
                            )
                            
                            if selected_metrics:
                                # Filter to selected metrics
                                plot_data = cat_melted[cat_melted['metric'].isin(selected_metrics)]
                                
                                # Create line chart
                                fig_trends = px.line(
                                    plot_data,
                                    x='event_dttm',
                                    y='value',
                                    color='metric',
                                    markers=True,
                                    title=f"{selected_category} Trends Over Time",
                                    labels={'event_dttm': 'Time', 'value': 'Value', 'metric': 'Metric'}
                                )
                                
                                # Add Day 0 line to trends chart
                                if 'enrollment_dttm' in patient_wide.columns and patient_wide['enrollment_dttm'].notna().any():
                                    enrollment_str = str(patient_wide['enrollment_dttm'].dropna().iloc[0])
                                    fig_trends.add_shape(
                                        type="line",
                                        x0=enrollment_str, x1=enrollment_str,
                                        y0=0, y1=1,
                                        yref="paper",
                                        line=dict(color='#e74c3c', width=2, dash='dash')
                                    )
                                    fig_trends.add_annotation(
                                        x=enrollment_str,
                                        y=1.05,
                                        yref="paper",
                                        text="<b>Day 0</b>",
                                        showarrow=False,
                                        font=dict(size=13, color='#e74c3c'),
                                        bgcolor="rgba(255,255,255,0.8)"
                                    )
                                
                                fig_trends.update_layout(
                                    height=450,
                                    title=dict(font=dict(size=20)),
                                    xaxis=dict(
                                        title=dict(text="Time", font=dict(size=16)),
                                        showgrid=True,
                                        gridcolor='rgba(128,128,128,0.2)',
                                        tickformat='%Y-%m-%d %H:%M',
                                        tickfont=dict(size=13)
                                    ),
                                    yaxis=dict(
                                        title=dict(text="Value", font=dict(size=16)),
                                        showgrid=True,
                                        gridcolor='rgba(128,128,128,0.2)',
                                        tickfont=dict(size=13)
                                    ),
                                    legend=dict(
                                        orientation="h",
                                        yanchor="bottom",
                                        y=-0.3,
                                        xanchor="center",
                                        x=0.5,
                                        font=dict(size=14)
                                    ),
                                    hovermode='x unified',
                                    plot_bgcolor='white',
                                    hoverlabel=dict(font_size=14)
                                )
                                
                                st.plotly_chart(fig_trends, use_container_width=True)
                                
                                # Show basic stats for selected metrics
                                st.markdown("##### Summary Statistics")
                                stats_data = []
                                for metric in selected_metrics:
                                    metric_values = plot_data[plot_data['metric'] == metric]['value']
                                    stats_data.append({
                                        'Metric': metric,
                                        'Count': len(metric_values),
                                        'Min': f"{metric_values.min():.2f}" if len(metric_values) > 0 else 'N/A',
                                        'Max': f"{metric_values.max():.2f}" if len(metric_values) > 0 else 'N/A',
                                        'Mean': f"{metric_values.mean():.2f}" if len(metric_values) > 0 else 'N/A',
                                        'Median': f"{metric_values.median():.2f}" if len(metric_values) > 0 else 'N/A'
                                    })
                                stats_df = pd.DataFrame(stats_data)
                                st.dataframe(stats_df, use_container_width=True, hide_index=True)
                            else:
                                st.info("Select at least one metric to display trends.")
                        else:
                            st.info(f"No data available for {selected_category}.")
                    else:
                        st.info(f"No data available for {selected_category}.")
                else:
                    st.info(f"No columns found for {selected_category}.")
            else:
                st.warning("No events found for visualization.")

# =============================================================================
# Run App
# =============================================================================
if __name__ == "__main__":
    main()

