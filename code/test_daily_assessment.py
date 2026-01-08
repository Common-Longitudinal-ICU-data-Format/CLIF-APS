#!/usr/bin/env python3
"""
test_daily_assessment.py
Test script to compare daily_assessment_upload.csv against test data
Compares values between test file and generated output file for daily assessment columns
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys
from datetime import datetime
import io

# File paths - using relative paths from code directory
PROJECT_ROOT = Path(__file__).parent.parent
TEST_FILE = PROJECT_ROOT / "tests" / "APSEDC_DATA.csv"
OUTPUT_FILE = PROJECT_ROOT / "output" / "final" / "daily_assessment_upload.csv"
MAPPING_FILE = PROJECT_ROOT / "data" / "static_df.parquet"  

# Create log file path with timestamp
LOG_DIR = PROJECT_ROOT / "output" / "final"
LOG_FILE = LOG_DIR / f"test_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

class Tee:
    """Class to duplicate stdout to both console and file"""
    def __init__(self, file_path):
        self.terminal = sys.stdout
        self.log = open(file_path, 'w')

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()

def load_data():
    """Load both CSV files"""
    print("=" * 80)
    print("LOADING DATA FILES")
    print("=" * 80)

    # Check if files exist
    if not TEST_FILE.exists():
        print(f"❌ ERROR: Test file not found: {TEST_FILE}")
        sys.exit(1)

    if not OUTPUT_FILE.exists():
        print(f"❌ ERROR: Output file not found: {OUTPUT_FILE}")
        sys.exit(1)

    # Load test data
    print(f"Loading test data from: {TEST_FILE.name}")
    test_df = pd.read_csv(TEST_FILE, low_memory=False)
    print(f"  ✓ Test data loaded: {test_df.shape[0]} rows, {test_df.shape[1]} columns")

    # Load output data
    print(f"Loading output data from: {OUTPUT_FILE.name}")
    output_df = pd.read_csv(OUTPUT_FILE, low_memory=False)
    print(f"  ✓ Output data loaded: {output_df.shape[0]} rows, {output_df.shape[1]} columns")

    return test_df, output_df

def get_common_columns(test_df, output_df):
    """Get columns that exist in both dataframes (excluding ID columns)"""
    test_cols = set(test_df.columns)
    output_cols = set(output_df.columns)

    # Find common columns
    common_cols = test_cols.intersection(output_cols)

    # Remove ID columns from comparison
    id_cols = {'record_id', 'redcap_event_name'}
    compare_cols = common_cols - id_cols

    # Get columns only in output
    output_only = output_cols - test_cols - id_cols

    # Get columns only in test
    test_only = test_cols - output_cols - id_cols

    return sorted(compare_cols), sorted(output_only), sorted(test_only)

def merge_dataframes(test_df, output_df):
    """Merge dataframes on record_id and redcap_event_name"""
    # Convert record_id to string in both dataframes to ensure compatibility
    test_df = test_df.copy()
    output_df = output_df.copy()

    test_df['record_id'] = test_df['record_id'].astype(str).str.strip()
    output_df['record_id'] = output_df['record_id'].astype(str).str.strip()

    # Also ensure redcap_event_name is string and strip whitespace
    if 'redcap_event_name' in test_df.columns:
        test_df['redcap_event_name'] = test_df['redcap_event_name'].astype(str).str.strip()
    if 'redcap_event_name' in output_df.columns:
        output_df['redcap_event_name'] = output_df['redcap_event_name'].astype(str).str.strip()

    # Debug: Show sample values from both dataframes
    print("\n📋 Debug Information:")
    print(f"Test file - First 5 record_ids: {test_df['record_id'].head().tolist()}")
    print(f"Output file - First 5 record_ids: {output_df['record_id'].head().tolist()}")
    print(f"\nTest file - Unique redcap_event_names: {test_df['redcap_event_name'].unique()[:5].tolist()}")
    print(f"Output file - Unique redcap_event_names: {output_df['redcap_event_name'].unique()[:5].tolist()}")

    # Check for common record_ids
    common_record_ids = set(test_df['record_id']) & set(output_df['record_id'])
    print(f"\nCommon record_ids found: {len(common_record_ids)}")
    if len(common_record_ids) > 0:
        print(f"Examples of common record_ids: {list(common_record_ids)[:5]}")

    # Check for common event names
    common_events = set(test_df['redcap_event_name']) & set(output_df['redcap_event_name'])
    print(f"\nCommon redcap_event_names found: {len(common_events)}")
    if len(common_events) > 0:
        print(f"Examples of common events: {list(common_events)[:5]}")

    # Merge on the common ID columns
    merged_df = pd.merge(
        test_df,
        output_df,
        on=['record_id', 'redcap_event_name'],
        how='inner',
        suffixes=('_test', '_output')
    )

    # If no matches, try to diagnose why
    if len(merged_df) == 0 and len(common_record_ids) > 0:
        print("\n⚠️  Found common record_ids but no matches after merge!")
        print("Checking combinations of record_id and redcap_event_name...")

        # Show combinations from test file
        test_combos = test_df[['record_id', 'redcap_event_name']].drop_duplicates().head(10)
        print("\nTest file combinations (first 10):")
        for _, row in test_combos.iterrows():
            print(f"  - record_id: '{row['record_id']}', event: '{row['redcap_event_name']}'")

        # Show combinations from output file
        output_combos = output_df[['record_id', 'redcap_event_name']].drop_duplicates().head(10)
        print("\nOutput file combinations (all):")
        for _, row in output_combos.iterrows():
            print(f"  - record_id: '{row['record_id']}', event: '{row['redcap_event_name']}'")

    return merged_df

def compare_columns(merged_df, columns):
    """Compare columns between test and output data"""
    results = {
        'passed': [],
        'failed': [],
        'errors': []
    }

    for col in columns:
        try:
            test_col = f"{col}_test" if f"{col}_test" in merged_df.columns else col
            output_col = f"{col}_output" if f"{col}_output" in merged_df.columns else col

            # Get the columns - handle case where column might only exist in one side
            if test_col in merged_df.columns and output_col in merged_df.columns:
                test_values = merged_df[test_col]
                output_values = merged_df[output_col]
            elif col in merged_df.columns:
                # Column exists without suffix
                test_values = merged_df[col]
                output_values = merged_df[col]
            else:
                results['errors'].append({
                    'column': col,
                    'error': 'Column not found in merged dataframe'
                })
                continue

            # Convert to numeric for comparison, treating errors as NaN
            test_numeric = pd.to_numeric(test_values, errors='coerce')
            output_numeric = pd.to_numeric(output_values, errors='coerce')

            # Compare values
            # Consider values equal if both are NaN or if they're close enough
            both_nan = test_numeric.isna() & output_numeric.isna()
            close_enough = np.isclose(test_numeric, output_numeric, rtol=1e-5, atol=1e-8, equal_nan=False)
            matches = both_nan | close_enough

            # Count matches
            total_records = len(matches)
            matching_records = matches.sum()
            mismatch_records = total_records - matching_records

            if mismatch_records == 0:
                results['passed'].append({
                    'column': col,
                    'total_records': total_records,
                    'matching': matching_records
                })
            else:
                # Find specific mismatches for debugging
                mismatch_indices = merged_df[~matches].index[:5]  # Get first 5 mismatches
                mismatches = []
                for idx in mismatch_indices:
                    record_id = merged_df.loc[idx, 'record_id']
                    event_name = merged_df.loc[idx, 'redcap_event_name']
                    test_val = test_values.iloc[idx] if idx < len(test_values) else None
                    output_val = output_values.iloc[idx] if idx < len(output_values) else None
                    mismatches.append({
                        'record_id': record_id,
                        'event': event_name,
                        'test_value': test_val,
                        'output_value': output_val
                    })

                results['failed'].append({
                    'column': col,
                    'total_records': total_records,
                    'matching': matching_records,
                    'mismatches': mismatch_records,
                    'examples': mismatches
                })

        except Exception as e:
            results['errors'].append({
                'column': col,
                'error': str(e)
            })

    return results

def print_results(results, merged_df, output_only_cols, test_only_cols):
    """Print test results in a clear format"""
    print("\n" + "=" * 80)
    print("TEST RESULTS SUMMARY")
    print("=" * 80)

    total_cols = len(results['passed']) + len(results['failed']) + len(results['errors'])

    print(f"\n📊 Overall Statistics:")
    print(f"  • Total columns compared: {total_cols}")
    print(f"  • Total records compared: {len(merged_df)}")
    print(f"  • Columns passed: {len(results['passed'])} ({len(results['passed'])/total_cols*100:.1f}%)")
    print(f"  • Columns failed: {len(results['failed'])} ({len(results['failed'])/total_cols*100:.1f}%)")
    print(f"  • Columns with errors: {len(results['errors'])} ({len(results['errors'])/total_cols*100:.1f}%)")

    # Print columns only in output
    if output_only_cols:
        print(f"\n📝 Columns only in output file ({len(output_only_cols)}):")
        for i, col in enumerate(output_only_cols[:10], 1):
            print(f"    {i}. {col}")
        if len(output_only_cols) > 10:
            print(f"    ... and {len(output_only_cols) - 10} more")

    # Print passed columns summary
    if results['passed']:
        print(f"\n✅ PASSED COLUMNS ({len(results['passed'])})")
        print("-" * 40)
        # Group by prefix for cleaner display
        prefixes = {}
        for item in results['passed']:
            prefix = item['column'].split('_')[0] + '_' + item['column'].split('_')[1] if '_' in item['column'] else item['column']
            if prefix not in prefixes:
                prefixes[prefix] = []
            prefixes[prefix].append(item['column'])

        for prefix, cols in sorted(prefixes.items())[:5]:
            print(f"  • {prefix}*: {len(cols)} columns match perfectly")

        if len(prefixes) > 5:
            print(f"  ... and {len(prefixes) - 5} more column groups")

    # Print failed columns with details
    if results['failed']:
        print(f"\n❌ FAILED COLUMNS ({len(results['failed'])})")
        print("-" * 40)
        for i, item in enumerate(results['failed'][:10], 1):
            print(f"\n  {i}. Column: {item['column']}")
            print(f"     • Records compared: {item['total_records']}")
            print(f"     • Matching: {item['matching']} ({item['matching']/item['total_records']*100:.1f}%)")
            print(f"     • Mismatches: {item['mismatches']} ({item['mismatches']/item['total_records']*100:.1f}%)")

            if item['examples']:
                print(f"     • Example mismatches (first {len(item['examples'])}):")
                for j, ex in enumerate(item['examples'][:3], 1):
                    print(f"       {j}. Record {ex['record_id']} ({ex['event']})")
                    print(f"          Test value: {ex['test_value']}")
                    print(f"          Output value: {ex['output_value']}")

        if len(results['failed']) > 10:
            print(f"\n  ... and {len(results['failed']) - 10} more failed columns")

    # Print errors if any
    if results['errors']:
        print(f"\n⚠️  COLUMNS WITH ERRORS ({len(results['errors'])})")
        print("-" * 40)
        for item in results['errors'][:5]:
            print(f"  • {item['column']}: {item['error']}")

        if len(results['errors']) > 5:
            print(f"  ... and {len(results['errors']) - 5} more errors")

    # Final verdict
    print("\n" + "=" * 80)
    if len(results['failed']) == 0 and len(results['errors']) == 0:
        print("🎉 ALL TESTS PASSED! 🎉")
        print("All compared columns match perfectly between test and output files.")
    else:
        print("⚠️  TESTS COMPLETED WITH ISSUES")
        print(f"Found {len(results['failed'])} column mismatches and {len(results['errors'])} errors.")
        print("Review the failed columns above for details.")
    print("=" * 80)

def save_detailed_report(results, output_only_cols):
    """Save detailed results to a CSV file for further analysis"""
    report_file = PROJECT_ROOT / "output" / "test_comparison_report.csv"

    # Create a detailed report dataframe
    report_data = []

    for item in results['passed']:
        report_data.append({
            'column': item['column'],
            'status': 'PASSED',
            'total_records': item['total_records'],
            'matching_records': item['matching'],
            'mismatch_count': 0,
            'match_percentage': 100.0
        })

    for item in results['failed']:
        report_data.append({
            'column': item['column'],
            'status': 'FAILED',
            'total_records': item['total_records'],
            'matching_records': item['matching'],
            'mismatch_count': item['mismatches'],
            'match_percentage': (item['matching'] / item['total_records'] * 100)
        })

    for item in results['errors']:
        report_data.append({
            'column': item['column'],
            'status': 'ERROR',
            'total_records': 0,
            'matching_records': 0,
            'mismatch_count': 0,
            'match_percentage': 0,
            'error': item['error']
        })

    if report_data:
        report_df = pd.DataFrame(report_data)
        report_df.to_csv(report_file, index=False)
        print(f"\n📄 Detailed report saved to: {report_file.relative_to(PROJECT_ROOT)}")

def main():
    """Main test execution"""
    # Ensure output directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Set up logging to both console and file
    tee = Tee(LOG_FILE)
    sys.stdout = tee

    print("\n" + "=" * 80)
    print(f"DAILY ASSESSMENT DATA COMPARISON TEST")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Log file: {LOG_FILE.relative_to(PROJECT_ROOT)}")
    print("=" * 80)

    try:
        # Load data
        test_df, output_df = load_data()

        # Get columns for comparison
        print("\n" + "=" * 80)
        print("ANALYZING COLUMNS")
        print("=" * 80)
        common_cols, output_only_cols, test_only_cols = get_common_columns(test_df, output_df)

        print(f"\n📋 Column Analysis:")
        print(f"  • Common columns to compare: {len(common_cols)}")
        print(f"  • Columns only in output: {len(output_only_cols)}")
        print(f"  • Columns only in test: {len(test_only_cols)}")

        if not common_cols:
            print("\n❌ ERROR: No common columns found to compare!")
            print("Please check that the files contain matching column names.")
            sys.exit(1)

        # Merge dataframes
        print("\n" + "=" * 80)
        print("MERGING DATAFRAMES")
        print("=" * 80)
        merged_df = merge_dataframes(test_df, output_df)
        print(f"  ✓ Merged {len(merged_df)} matching records")

        if len(merged_df) == 0:
            print("\n❌ ERROR: No matching records found!")
            print("Please check that record_id and redcap_event_name match between files.")
            sys.exit(1)

        # Compare columns
        print("\n" + "=" * 80)
        print("COMPARING DATA")
        print("=" * 80)
        print(f"Comparing {len(common_cols)} columns across {len(merged_df)} records...")

        results = compare_columns(merged_df, common_cols)

        # Print results
        print_results(results, merged_df, output_only_cols, test_only_cols)

        # Save detailed report
        save_detailed_report(results, output_only_cols)

        # Return exit code based on results
        exit_code = 1 if (len(results['failed']) > 0 or len(results['errors']) > 0) else 0

    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        exit_code = 1

    finally:
        # Restore stdout and close log file
        print(f"\n📝 Full test log saved to: {LOG_FILE.relative_to(PROJECT_ROOT)}")
        sys.stdout = tee.terminal
        tee.close()

        # Exit with appropriate code
        sys.exit(exit_code)

if __name__ == "__main__":
    main()