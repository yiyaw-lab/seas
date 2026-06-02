def generate_experiment(signal):
    title = signal["title"]
    capability = signal.get(
        "possible_capability_unlocked",
        "Unknown Capability"
    )

    return {
        "title": f"Explore: {title}",
        "source_signal": title,
        "capability": capability,
        "why_now": f"{title} scored highly enough to clear the SEAS action threshold.",
        "time_scope": "Under 10 hours",
        "artifact": f"A working prototype or demo that demonstrates {capability}",
        "completion": f"Show practical use of {title} through a concrete artifact",
        "build_steps": [
            "Define the smallest useful version",
            "Set up the project structure",
            "Build the core workflow",
            "Test with one real use case",
            "Document what worked and what failed"
        ],
        "failure_risks": [
            "Experiment becomes too broad",
            "Signal is interesting but not actionable",
            "Prototype does not demonstrate real capability"
        ],
        "fallback_plan": "Reduce scope to a written architecture and one minimal working demo.",
        "reflection_prompt": "What can I now do that I could not do before?",
        "possible_public_output": "A short build-in-public post explaining the experiment and what it taught."
    }
