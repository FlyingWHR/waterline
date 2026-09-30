# Waterline · demo script (about 3:20)

Say the lines in quotes. `[SLIDE n]` means the PDF deck (`docs/demo/waterline-deck.pdf`). `[SITE]` means the live
panel at https://waterline-eth.vercel.app. `[TERMINAL]` means a pod or your laptop. Slides 3 to 5 are backups if a
live step fails.

---

## 1 · Problem · 0:00 (25 s) · [SLIDE 1 → 2]

"GPUs are becoming a financial asset: a four-hundred-billion-dollar rental market, loans backed by GPUs, H100
futures due on CME in October. Yet delivery is still self-reported. There is no independent, shared record."

## 2 · Check · 0:25 (30 s) · [SITE Overview → TERMINAL A1]

Do: on the Overview hero pick **cloud-a** and **H100 SXM**, press Copy, paste it in A1's terminal.

"Waterline changes that. Pick your provider and GPU, copy one line, run it in the rented machine. Seconds later:
pass. The right chip, on time, already onchain under this GPU's own ENS name. And it re-checks at random times, so
the host can't just behave for the test."

Point at: PASS, the `gpu-…cloud-a.waterline.eth` name, the transaction.

## 3 · The exam · 0:55 (25 s) · [SITE · A1's check page]

Do: open the check from the receipt link. Point at the deadline bar, the core staircase, and the re-graded rows.

"Under the hood: a fresh problem, a clock on our side, answers sealed before we pick which to check. Then we count
cores. Heat slows a chip; it can't remove cores. Cores name the chip. The clock measures delivery."

## 4 · Fail · Publish · 1:20 (45 s) · [TERMINAL laptop → SITE]

Do: run the agent on B2 with `--web`, open the printed link, press Publish with your listing, paste the listing,
tick, Publish.

"Now a cheat. This pod is listed as an H100; it's an A100 I relabelled myself, playing the dishonest host. A hundred
and eight cores, no FP8: caught, and my agent stops paying. A failure accuses someone, so it goes public with the
listing I rented, in its own words. Jev reads it: it says H100, so the claim stands. Publish, and it's onchain."

## 5 · Record · Compare · 2:05 (45 s) · [SITE GPUs → Providers → B2's check]

Do: GPUs tab (the name tree), then the Providers tab, then Verify on B2's check. If `runpod.waterline.eth` shows,
say: "that one is my own test report, flagged automatically."

"It lives on ENS. Every GPU is named under its provider, with no registrations: our contract answers for the whole
tree. This one is suspect: one report, and it takes two. The report also counts against the provider, so renaming
the chip changes nothing. Side by side, the same H100 listing delivers this share of its rating here, and this
there. All onchain, all checkable against the report's hash."

## 6 · Use · 2:50 (20 s) · [MultiBaas console → TERMINAL]

Do: in the MultiBaas console, point at B2's failure, read from the contract. Then run
`python -m agent choose --listings docs/demo/listings.json` and point at the two lines: skips B2, rents A1.

"So what's the record good for? Next time I rent, my agent looks it up first. Curvegrid's MultiBaas reads every
result from our contract and keeps a tally for each GPU. The cheapest H100 on offer is the one we just caught, so the
agent skips it and rents the one that passed. It goes by the record, not an AI's opinion."

## 7 · Close · 3:10 (15 s) · [SLIDE 6]

"Waterline. Proof of delivered compute. Any renter can check, and the result stays on the GPU's name for whoever
needs it next: a renter, a lender, an agent, or even a GPU credit provider."

---

Before recording: A1 and B2 rented and reachable. The laptop has
`WATERLINE_API=https://waterline-eth.vercel.app`. The MultiBaas console and an ENS explorer are open in tabs.
