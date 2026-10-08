// Test fixture only: the registry as it looks with the demo feature installed. slots.test.tsx
// mocks ui/src/ext/registry with it; it can also stand in for the registry in a browser check.
import i18n from '../../../src/language/i18n'
import {addLocales, collectFeatures} from '../../../src/ext/collect'
import '../../../src/ext/ext.css'
import * as demo from './features/demo/index'
import demoStrings from './features/demo/locales/en.json'
import sharedStrings from '../../../src/ext/shared/locales/en.json'

addLocales(i18n, {'./features/demo/locales/en.json': demoStrings, './shared/locales/en.json': sharedStrings})

export const {features, problems, routes, navLinks, episodeHeaders, episodeTools, settingsTabs, rowBadges, playerActions, homeSections} =
    collectFeatures({'./features/demo/index.tsx': demo})
