import {describe, expect, it} from 'vitest'
import i18next from 'i18next'
import {addLocales, collectFeatures} from '../../src/ext/collect'
import type {Feature} from '../../src/ext/types'

const fixtures = import.meta.glob<{feature?: Feature}>('./fixtures/features/*/index.tsx', {eager: true})
const fixtureLocales = import.meta.glob<Record<string, unknown>>('./fixtures/features/*/locales/*.json', {eager: true, import: 'default'})

describe('feature registry', () => {
    it('picks up a feature folder and fills every slot from it', () => {
        const registry = collectFeatures(fixtures)
        expect(registry.features.map(feature => feature.id)).toEqual(['demo'])
        expect(registry.routes.map(route => route.path)).toEqual(['demo'])
        expect(registry.navLinks.map(link => [link.featureId, link.path, link.label, !!link.mobile]))
            .toEqual([['demo', '/demo', 'nav', true], ['demo', '/demo/desk', 'nav-desk', false]])
        expect(registry.episodeHeaders).toHaveLength(1)
        expect(registry.episodeTools.map(tool => tool.key)).toEqual(['demo:tool'])
        expect(registry.settingsTabs.map(tab => tab.path)).toEqual(['demo'])
        expect(registry.rowBadges).toHaveLength(1)
        expect(registry.playerActions.map(action => action.key)).toEqual(['demo:0'])
        expect(registry.homeSections.map(section => section.featureId)).toEqual(['demo'])
    })

    it('skips a malformed feature and says why', () => {
        const {problems} = collectFeatures(fixtures)
        expect(problems).toHaveLength(2)
        expect(problems.find(p => p.includes('no-export'))).toMatch(/does not export `feature`.*not loaded/)
        expect(problems.find(p => p.includes('wrong-id'))).toMatch(/"something-else" must equal its folder name "wrong-id"/)
    })

    it('loads a feature locale file as the namespace of the feature id', async () => {
        const i18n = i18next.createInstance()
        await i18n.init({lng: 'en', fallbackLng: 'en', resources: {}})
        expect(addLocales(i18n, fixtureLocales)).toEqual([])
        expect(i18n.t('tool', {ns: 'demo'})).toBe('Demo tool')
        expect(i18n.getFixedT('en', 'demo')('header', {id: 'e1', position: 12})).toBe('Demo header for e1 at 12')
        expect(addLocales(i18n, {'./features/demo/locales/en.json': ['not', 'an', 'object']})).toHaveLength(1)
    })

    it('refuses duplicate slots between features', () => {
        const demo = fixtures['./fixtures/features/demo/index.tsx']!.feature!
        const registry = collectFeatures({
            './features/demo/index.tsx': {feature: demo},
            './features/copy/index.tsx': {feature: {...demo, id: 'copy', episodeTools: []}},
        })
        expect(registry.features.map(feature => feature.id)).toEqual(['copy', 'demo'])
        expect(registry.routes).toHaveLength(1)
        expect(registry.problems.some(p => p.includes('route "demo" is already used'))).toBe(true)
    })

    it('the shipped registry loads every real feature without problems', async () => {
        const registry = await import('../../src/ext/registry')
        expect(registry.problems).toEqual([])
        const i18n = (await import('../../src/language/i18n')).default
        expect(i18n.getFixedT('en', 'shared')('make-transcript')).toBe('Make a transcript')
    })
})
