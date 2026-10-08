import type {i18n as I18n} from 'i18next'
import type {ComponentType, ReactNode} from 'react'
import type {EpisodeCtx, Feature} from './types'

type NavLinkDef = NonNullable<Feature['navLinks']>[number]

/** Every slot, flattened across features and tagged with the feature id (its i18next namespace). */
export type Registry = {
    features: Feature[]
    problems: string[]
    routes: {featureId: string; path: string; element: ReactNode}[]
    navLinks: (NavLinkDef & {featureId: string})[]
    episodeHeaders: {featureId: string; Component: ComponentType<EpisodeCtx>}[]
    episodeTools: {featureId: string; key: string; label: string; Component: ComponentType<EpisodeCtx>}[]
    settingsTabs: {featureId: string; path: string; label: string; element: ReactNode}[]
    rowBadges: {featureId: string; Component: ComponentType<{episodeId: string}>}[]
    playerActions: {featureId: string; key: string; Component: ComponentType<{episodeId: string; position: number}>}[]
    homeSections: {featureId: string; Component: ComponentType}[]
}

/** Namespace of ui/src/ext/shared/locales; no feature may take this id. */
export const SHARED_NAMESPACE = 'shared'
const ID = /^[a-z][a-z0-9-]*$/
const FEATURE_FILE = /\/features\/([^/]+)\/index\.tsx$/
const LOCALE_FILE = /\/(?:features\/([^/]+)|shared)\/locales\/([^/]+)\.json$/
const relative = (path: string) => path.replace(/^\/+/, '')

function problemWith(feature: Feature | undefined, folder: string | undefined, loaded: Feature[]): string {
    if (!feature) return 'it does not export `feature`'
    if (feature.id !== folder) return `its id "${feature.id}" must equal its folder name "${folder}"`
    if (!ID.test(feature.id)) return 'its id must use lowercase letters, digits and dashes'
    if (feature.id === SHARED_NAMESPACE) return `the id "${SHARED_NAMESPACE}" is reserved`
    if (loaded.some(other => other.id === feature.id)) return 'its id is already used'
    return ''
}

/** Collect `feature` exports from `import.meta.glob('./features/*\/index.tsx', {eager: true})`. */
export function collectFeatures(modules: Record<string, {feature?: Feature}>): Registry {
    const r: Registry = {features: [], problems: [], routes: [], navLinks: [], episodeHeaders: [],
        episodeTools: [], settingsTabs: [], rowBadges: [], playerActions: [], homeSections: []}
    const taken = new Set<string>()
    const claim = (kind: string, value: string, file: string) => {
        if (!taken.has(kind + value)) {taken.add(kind + value); return true}
        r.problems.push(`${file}: ${kind} "${value}" is already used by another feature; it was left out.`)
        return false
    }
    for (const file of Object.keys(modules).sort()) {
        const feature = modules[file]?.feature
        const problem = problemWith(feature, FEATURE_FILE.exec(file)?.[1], r.features)
        if (!feature || problem) {r.problems.push(`${file}: ${problem}. The feature was not loaded.`); continue}
        const featureId = feature.id
        r.features.push(feature)
        for (const route of feature.routes ?? []) {
            const path = relative(route.path)
            if (claim('route', path, file)) r.routes.push({featureId, path, element: route.element})
        }
        for (const link of feature.navLinks ?? []) {
            const path = '/' + relative(link.path)
            if (claim('nav link', path, file)) r.navLinks.push({...link, path, featureId})
        }
        if (feature.episodeHeader) r.episodeHeaders.push({featureId, Component: feature.episodeHeader})
        for (const tool of feature.episodeTools ?? []) {
            const key = `${featureId}:${tool.id}`
            if (claim('episode tool', key, file)) r.episodeTools.push({featureId, key, label: tool.label, Component: tool.Component})
        }
        for (const tab of feature.settingsTabs ?? []) {
            const path = relative(tab.path).replace(/^settings\//, '')
            if (claim('settings tab', path, file)) r.settingsTabs.push({featureId, path, label: tab.label, element: tab.element})
        }
        if (feature.rowBadges) r.rowBadges.push({featureId, Component: feature.rowBadges})
        feature.playerActions?.forEach((Component, index) => r.playerActions.push({featureId, key: `${featureId}:${index}`, Component}))
        if (feature.homeSection) r.homeSections.push({featureId, Component: feature.homeSection})
    }
    return r
}

/** Load `locales/<language>.json` files into i18next: a feature's under its id, shared ones under "shared". */
export function addLocales(i18n: I18n, files: Record<string, unknown>): string[] {
    const problems: string[] = []
    for (const file of Object.keys(files).sort()) {
        const match = LOCALE_FILE.exec(file), strings = files[file]
        if (!match || !strings || typeof strings !== 'object' || Array.isArray(strings)) {
            problems.push(`${file}: expected an object in locales/<language>.json; it was not loaded.`)
            continue
        }
        i18n.addResourceBundle(match[2]!, match[1] ?? SHARED_NAMESPACE, strings, true, true)
    }
    return problems
}
