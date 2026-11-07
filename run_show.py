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

import llm_client  # noqa: F401 - loads .env

HERE = Path(__file__).resolve().parent


def step(work: Path, script: str, *args: str, must_pass: bool = True) -> int:
    print(f"\n== {script} {' '.join(args)}", flush=True)
    code = subprocess.run([sys.executable, str(HERE / script), *args], cwd=work).returncode
    if code and must_pass:
        sys.exit(f"{script} failed with exit code {code}")
    return code


def main():
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    episodes = Path(args[0]).resolve()
    work = Path(args[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)

    step(work, "parser.py", str(episodes), "data/parsed")
    step(work, "heuristics.py", "data/parsed", "data/candidates")
    if "--prepare-only" in flags:
        print(f"\nPrepared in {work}. Run without --prepare-only to judge.")
        return
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PROMO_FINDER_API_KEY")):
        sys.exit("Set ANTHROPIC_API_KEY, or PROMO_FINDER_BASE_URL and PROMO_FINDER_API_KEY, to run the judgment step.")

    step(work, "llm_scorer.py", "data/parsed", "data/candidates", "data/promos")
    # The scorer has already dropped invalid picks; these reports are for the record.
    step(work, "validate.py", "data/promos", "data/parsed", must_pass=False)
    step(work, "critic.py", "data/promos", "data/promos_scored")
    step(work, "spoiler_check.py", "data/promos_scored")
    step(work, "rank.py", "data/promos_scored")
    step(work, "global_rank.py")
    step(work, "rank.py", "data/promos_scored")
    step(work, "cross_episode.py", "propose")
    step(work, "validate.py", "--montages", "outputs/cross_episode_promos.json", "data/parsed", must_pass=False)
    print(f"\nDone. See {work / 'outputs' / 'ranked_promos.md'}")


if __name__ == "__main__":
    main()
