<!--
Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
SPDX-License-Identifier: MIT
Contributors: KC
Maintainer: KC
-->

# RAVE2 App Store — Format Specifications

Welcome. This folder is the reference for the two file formats you'll work with
when you publish software to a RAVE2 device:

- **[The App Store catalog](appstore-catalog-format.md)** (`appstore.json`) — the
  list a device reads to find out what apps exist, what they're called, and where
  to download them.
- **[The application bundle](rave2app-bundle-format.md)** (`.rave2app`) — the
  actual package a device downloads and installs.

If you're new here, read them in that order. The catalog is the menu; the bundle
is the meal.

## The one-paragraph mental model

A RAVE2 device shows an App Store. That App Store is driven by a single JSON
file — the **catalog** — that you host somewhere reachable. Each entry in the
catalog points at a **bundle**: a self-contained `.rave2app` file that carries
everything the device needs to install your application. The catalog is
deliberately thin (just enough to list and download an app); the bundle is
self-describing and authoritative about how it installs. Keeping those two jobs
separate means you can re-skin the store, move downloads around, or evolve what's
inside a bundle without the two formats stepping on each other.

## What stays stable, and what doesn't

Both formats carry a version number so we can evolve them without breaking older
devices:

- The catalog has a top-level `schema_version`.
- Each bundle declares a `format_version` inside its descriptor.

We only bump these when a change would break an older reader. When we do, this
folder is updated in the same change, so treat these documents as the source of
truth — not any one example file you happen to have lying around.

## A note on examples

The snippets in these documents are real and current. If you copy one as a
starting point, fill in your own values and double-check it against the rules in
the same document before you publish.
