# Adding a feature to the UI

1. Create `features/<id>/index.tsx` exporting `feature: Feature` (see `types.ts`), with `id` equal to the folder name.
2. Fill only the slots you need: `routes` (under `/`), `navLinks` (under More on desktop and phones;
   `badge` shows a count), `homeSection` (top of Listen now), `episodeHeader` and
   `episodeTools` (the episode workspace), `settingsTabs` (under `/settings`), `rowBadges` (episode rows) and
   `playerActions` (the player bar). `registry.ts` finds the folder by itself; don't edit `App.tsx`, `Sidebar.tsx`,
   `HomePageSelector.tsx`, `Learn.tsx`, `SettingsPage.tsx`, `ListenEpisodeRow.tsx` or `DrawerAudioPlayer.tsx`.
3. Put your strings in `features/<id>/locales/en.json`. They load as the i18next namespace `<id>`: call
   `useTranslation('<id>')` in components, and use keys (not text) for the labels in `feature`.
4. Call our API with `companion<T>(path, init)` from `utils/companion.ts`, and keep your types in your folder.
5. For "no transcript yet", render `shared/MakeTranscript.tsx` (`<MakeTranscript key={episode.id} episode={episode} onReady={refetch}/>`).
6. Episode ids in slots are the episode's `episode_id` (the id in `/companion/episodes/{id}`). Times are seconds
   from the start of the original episode audio.
7. Test with a fixture feature under `ui/test/ext/…` (see `registry.test.ts`); never ship a demo feature.
   A malformed feature is skipped and logged as `[ext] …` in the browser console, and that test fails.
