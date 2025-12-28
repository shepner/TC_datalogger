#!/usr/bin/env python3
"""
Automatic commit script for Torn City Faction Crimes project.

This script automatically commits changes when:
1. Significant changes have occurred (new/modified source files, config, docs)
2. It has been more than 24 hours since the last commit

The script respects .gitignore and will not commit sensitive files.
"""

import subprocess
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional


# Files/directories that indicate significant changes
SIGNIFICANT_PATTERNS = [
    'src/',
    'tests/',
    'config/',
    'requirements.txt',
    'Dockerfile',
    'docker-compose.yml',
    '*.py',
    '*.md',
    '*.json',
    '*.yml',
    '*.yaml',
    '.gitignore',
    '.env.example',
]

# Files/directories to ignore (even if they match significant patterns)
IGNORE_PATTERNS = [
    'logs/',
    '*.log',
    '.env',
    'config/credentials.json',
    'config/TC_API_config.json',  # May contain sensitive data
    '__pycache__/',
    '*.pyc',
    '.git/',
    'venv/',
    '.venv/',
]

# Maximum time since last commit before auto-committing (in hours)
MAX_HOURS_SINCE_COMMIT = 24


def run_git_command(cmd: List[str], check: bool = True) -> Tuple[int, str, str]:
    """Run a git command and return the result."""
    try:
        result = subprocess.run(
            ['git'] + cmd,
            capture_output=True,
            text=True,
            check=check,
            cwd=os.getcwd()
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.CalledProcessError as e:
        return e.returncode, e.stdout.strip(), e.stderr.strip()


def get_last_commit_time() -> Optional[datetime]:
    """Get the timestamp of the last commit."""
    returncode, stdout, stderr = run_git_command(['log', '-1', '--format=%ct'], check=False)
    if returncode != 0 or not stdout:
        return None
    
    try:
        timestamp = int(stdout)
        return datetime.fromtimestamp(timestamp)
    except (ValueError, OSError):
        return None


def get_changed_files() -> List[str]:
    """Get list of changed and untracked files."""
    changed_files = []
    
    # Get modified and staged files
    returncode, stdout, _ = run_git_command(['diff', '--name-only', '--cached'], check=False)
    if stdout:
        changed_files.extend(stdout.split('\n'))
    
    returncode, stdout, _ = run_git_command(['diff', '--name-only'], check=False)
    if stdout:
        changed_files.extend(stdout.split('\n'))
    
    # Get untracked files
    returncode, stdout, _ = run_git_command(['ls-files', '--others', '--exclude-standard'], check=False)
    if stdout:
        changed_files.extend(stdout.split('\n'))
    
    # Remove duplicates and empty strings
    return list(set(filter(None, changed_files)))


def is_significant_file(filepath: str) -> bool:
    """Check if a file represents a significant change."""
    # Check ignore patterns first
    for pattern in IGNORE_PATTERNS:
        if pattern in filepath or filepath.startswith(pattern.rstrip('/')):
            return False
    
    # Check if file matches significant patterns
    for pattern in SIGNIFICANT_PATTERNS:
        if pattern.endswith('/'):
            if filepath.startswith(pattern):
                return True
        elif pattern.startswith('*.'):
            if filepath.endswith(pattern[1:]):
                return True
        else:
            if pattern in filepath or filepath == pattern:
                return True
    
    return False


def has_significant_changes() -> bool:
    """Check if there are any significant changes."""
    changed_files = get_changed_files()
    return any(is_significant_file(f) for f in changed_files)


def generate_commit_message() -> str:
    """Generate a commit message based on changed files."""
    changed_files = get_changed_files()
    significant_files = [f for f in changed_files if is_significant_file(f)]
    
    if not significant_files:
        return "Auto-commit: Minor changes"
    
    # Categorize changes
    categories = {
        'source': [],
        'tests': [],
        'config': [],
        'docs': [],
        'docker': [],
        'other': [],
    }
    
    for filepath in significant_files:
        if filepath.startswith('src/'):
            categories['source'].append(filepath)
        elif filepath.startswith('tests/'):
            categories['tests'].append(filepath)
        elif filepath.startswith('config/'):
            categories['config'].append(filepath)
        elif filepath.endswith('.md'):
            categories['docs'].append(filepath)
        elif 'docker' in filepath.lower() or filepath in ['Dockerfile', 'docker-compose.yml']:
            categories['docker'].append(filepath)
        else:
            categories['other'].append(filepath)
    
    # Build commit message
    parts = []
    if categories['source']:
        parts.append(f"Update source code ({len(categories['source'])} file(s))")
    if categories['tests']:
        parts.append(f"Update tests ({len(categories['tests'])} file(s))")
    if categories['config']:
        parts.append(f"Update configuration ({len(categories['config'])} file(s))")
    if categories['docs']:
        parts.append(f"Update documentation ({len(categories['docs'])} file(s))")
    if categories['docker']:
        parts.append(f"Update Docker configuration ({len(categories['docker'])} file(s))")
    if categories['other']:
        parts.append(f"Update other files ({len(categories['other'])} file(s))")
    
    message = "Auto-commit: " + ", ".join(parts)
    
    # Add file list if not too long
    if len(significant_files) <= 10:
        message += f"\n\nChanged files:\n" + "\n".join(f"- {f}" for f in significant_files)
    
    return message


def should_auto_commit() -> Tuple[bool, str]:
    """Determine if an auto-commit should be performed."""
    # Check if working directory is clean
    returncode, stdout, _ = run_git_command(['status', '--porcelain'], check=False)
    if returncode != 0 or not stdout.strip():
        return False, "Working directory is clean, no changes to commit"
    
    # Check for significant changes
    if not has_significant_changes():
        return False, "No significant changes detected"
    
    # Check time since last commit
    last_commit_time = get_last_commit_time()
    if last_commit_time:
        hours_since_commit = (datetime.now() - last_commit_time).total_seconds() / 3600
        if hours_since_commit < MAX_HOURS_SINCE_COMMIT:
            return True, f"Significant changes detected (last commit was {hours_since_commit:.1f} hours ago)"
    else:
        # No previous commits, commit if there are changes
        return True, "No previous commits found, committing initial changes"
    
    # If it's been too long, commit regardless
    if last_commit_time:
        hours_since_commit = (datetime.now() - last_commit_time).total_seconds() / 3600
        if hours_since_commit >= MAX_HOURS_SINCE_COMMIT:
            return True, f"More than {MAX_HOURS_SINCE_COMMIT} hours since last commit"
    
    return True, "Significant changes detected"


def main():
    """Main function to perform auto-commit."""
    # Check if we're in a git repository
    returncode, _, _ = run_git_command(['rev-parse', '--git-dir'], check=False)
    if returncode != 0:
        print("Error: Not in a git repository", file=sys.stderr)
        sys.exit(1)
    
    # Determine if we should commit
    should_commit, reason = should_auto_commit()
    
    if not should_commit:
        print(f"Skipping auto-commit: {reason}")
        sys.exit(0)
    
    print(f"Auto-committing changes: {reason}")
    
    # Stage all changes
    returncode, stdout, stderr = run_git_command(['add', '-A'], check=False)
    if returncode != 0:
        print(f"Error staging files: {stderr}", file=sys.stderr)
        sys.exit(1)
    
    # Generate commit message
    commit_message = generate_commit_message()
    
    # Commit changes
    returncode, stdout, stderr = run_git_command(
        ['commit', '-m', commit_message],
        check=False
    )
    
    if returncode != 0:
        if 'nothing to commit' in stderr.lower():
            print("No changes to commit after staging")
            sys.exit(0)
        print(f"Error committing changes: {stderr}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Successfully committed changes")
    print(f"Commit message: {commit_message.split(chr(10))[0]}")
    sys.exit(0)


if __name__ == '__main__':
    main()

