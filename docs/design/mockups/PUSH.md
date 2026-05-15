# How to push this branch

My environment is read-only to GitHub — I can pull files in but can't commit or push. Here's the three-command path to land this on `design/marketpulse-v1-mockups`:

```bash
# from the root of your MarketPulse clone
git checkout -b design/marketpulse-v1-mockups
mkdir -p docs/design
cp -r /path/to/this/download/mockups docs/design/

git add docs/design/mockups
git commit -m "design: NineScrolls v1 mockups — 3 stock variants + /trades + /holdings + /recap"
git push -u origin design/marketpulse-v1-mockups
```

Then open the branch on GitHub:
`https://github.com/divxer/MarketPulse/tree/design/marketpulse-v1-mockups/docs/design/mockups`

Open a PR if you want feedback before merging, or leave it as a long-lived design branch for reference. The mockups don't touch any production code so merging is low-risk; the natural home is as a permanent `docs/design/` reference for the v2 implementation.
