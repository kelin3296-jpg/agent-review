from setuptools import find_packages, setup


setup(
    name="agent-review",
    version="0.1.0",
    description="Local multi-agent review gate for Claude Code, Codex, and Hermes.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="agent-review contributors",
    python_requires=">=3.9",
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={
        "agent_review": [
            "prompts/*.md",
            "schemas/*.json",
        ],
    },
    entry_points={
        "console_scripts": [
            "agent-review=agent_review.cli:main",
        ],
    },
)
