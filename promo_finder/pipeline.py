"""
One command for a whole new show. Each show gets its own work folder, so its
episodes never mix with another show's.

  python run_show.py "<folder of episode .txt files>" <work_folder>
  python run_show.py "<folder>" <work_folder> --prepare-only   # parse + candidates, no API key needed

Results land in <work_folder>/outputs/. Re-running resumes: finished episodes are skipped.
"""
import os
import subprocess
import sys
from pathlib import Path

from promo_finder.llm import client as llm_client  # noqa: F401 - loads .env

ROOT = Path(__file__).resolve().parents[1]


def step(work: Path, module: str, *args: str, must_pass: bool = True) -> int:
    """Each step runs in the show's own folder, so its files never mix with another show's."""
    print(f"\n== {module} {' '.join(args)}", flush=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    code = subprocess.run([sys.executable, "-m", f"promo_finder.{module}", *args], cwd=work, env=env).returncode
    if code and must_pass:
        sys.exit(f"{module} failed with exit code {code}")
    return code


def main():
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    episodes = Path(args[0]).resolve()
    work = Path(args[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)

    step(work, "ingest.parser", str(episodes), "data/parsed")
    step(work, "ingest.heuristics", "data/parsed", "data/candidates")
    if "--prepare-only" in flags:
        print(f"\nPrepared in {work}. Run without --prepare-only to judge.")
        return
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PROMO_FINDER_API_KEY")):
        sys.exit("Set ANTHROPIC_API_KEY, or PROMO_FINDER_BASE_URL and PROMO_FINDER_API_KEY, to run the judgment step.")

    step(work, "judge.scorer", "data/parsed", "data/candidates", "data/promos")
    # The scorer has already dropped invalid picks; these reports are for the record.
    step(work, "judge.validate", "data/promos", "data/parsed", must_pass=False)
    step(work, "judge.critic", "data/promos", "data/promos_scored")
    step(work, "judge.spoiler", "data/promos_scored")
    step(work, "rank.ranker", "data/promos_scored")
    step(work, "rank.post_first")
    step(work, "rank.ranker", "data/promos_scored")
    step(work, "rank.montages", "propose")
    step(work, "judge.validate", "--montages", "outputs/cross_episode_promos.json", "data/parsed", must_pass=False)
    print(f"\nDone. See {work / 'outputs' / 'ranked_promos.md'}")


if __name__ == "__main__":
    main()
