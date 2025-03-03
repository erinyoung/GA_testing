import json
from collections import Counter

# Load the JSON file
with open("data.json") as f:  # Replace with your actual file path
    data = json.load(f)

# Initialize a Counter to store repository counts
repo_counts = Counter()

# Iterate through the logs and count occurrences of each repository
for log in data["logs"]:
    repo = log.get('metadata', {}).get("repo")
    if repo:  # Only count if repo is present
        repo_counts[repo] += 1

# Sort the results by count (descending)
sorted_repo_counts = sorted(repo_counts.items(), key=lambda x: x[1], reverse=True)

# Print the sorted results
for repo, count in sorted_repo_counts:
    print(f"{repo}: {count}")