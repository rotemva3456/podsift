// Finds every feature in ./features and exposes its pieces to the slots in the app.
// How to add a feature: see README.md in this folder.
import i18n from '../language/i18n'
import {addLocales, collectFeatures} from './collect'
import type {Feature} from './types'
import './ext.css'

const registry = collectFeatures(import.meta.glob<{feature?: Feature}>('./features/*/index.tsx', {eager: true}))
const localeProblems = addLocales(i18n, import.meta.glob<Record<string, unknown>>(
    ['./features/*/locales/*.json', './shared/locales/*.json'], {eager: true, import: 'default'}))

/** Why a feature (or its strings) was not loaded. Empty when every feature is well formed. */
export const problems = [...registry.problems, ...localeProblems]
problems.forEach(problem => console.error(`[ext] ${problem}`))

export const {features, routes, navLinks, episodeHeaders, episodeTools, settingsTabs, rowBadges, playerActions, homeSections} = registry
