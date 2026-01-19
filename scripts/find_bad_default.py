import os
import re

def find_bad_default_usage(root_dir):
    # Regex for |default without a colon immediately following, or at end of tag
    # Capture patterns like: |default}} or |default }} or |default with space but no colon
    # Correct: |default:"value" or |default:variable
    # Bad: |default or |default }
    
    # Simple regex: looks for |default followed by something that is NOT a colon
    pattern = re.compile(r'\|default([^:]|$)') 
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.endswith('.html'):
                filepath = os.path.join(dirpath, filename)
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    for i, line in enumerate(lines):
                        if '|default' in line:
                            # Check strict adherence
                            # We want to find cases where it is NOT |default:
                            # But be careful with |default_if_none which is valid
                            
                            # Let's just print every usage of default to see
                            print(f"{filepath}:{i+1}: {line.strip()}")

if __name__ == "__main__":
    print("Scanning for default usage...")
    find_bad_default_usage("apps")
