def generate_experiment(signal):
    title = signal["title"]

    return {
        "title": f"Explore: {title}",
        "capability": signal.get(
            "possible_capability_unlocked",
            "Unknown Capability"
        ),
        "artifact": f"Prototype related to {title}",
        "completion": f"Demonstrate practical application of {title}"
    }
