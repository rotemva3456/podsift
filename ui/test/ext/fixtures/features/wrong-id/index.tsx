// Test fixture only: its id does not match its folder, so the registry must skip it.
import type {Feature} from '../../../../../src/ext/types'

export const feature: Feature = {id: 'something-else', routes: [{path: 'demo', element: null}]}
