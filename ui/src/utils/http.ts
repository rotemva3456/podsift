// Modified by Podsift contributors, 2026-09-24: the login header rule is shared (ext/shared/podfetch.ts). See CHANGES.md.
import createClient, {Middleware} from "openapi-fetch";
import createTanstackQueryClient from "openapi-react-query";
import {components, paths} from "../../schema";
import {APIError} from "./ErrorDefinition";
import { enqueueSnackbar } from "@/utils/toast";
import i18n from "../language/i18n";
import {getLogin} from "./login";
import {getConfigFromHtmlFile} from "./config";
import {authHeader} from "../ext/shared/podfetch";
import {hostedFetch} from '../ext/features/hosted-auth/session'

// The login header rule, shared with podfetch() and companion(): Basic or Bearer from the stored login.
export {authHeader}


export let apiURL: string
export let uiURL: string
if (window.location.pathname.startsWith("/ui")) {
    apiURL = window.location.protocol + "//" + window.location.hostname + ":" + window.location.port
} else {
    //match everything before /ui
    const regex = /\/([^/]+)\/ui\//
    const prefix = regex.exec(window.location.href)?.[1]
    apiURL = window.location.origin + (prefix ? "/" + prefix : '')
}
uiURL = window.location.protocol + "//" + window.location.hostname + ":" + window.location.port + "/ui"

export const client = createClient<paths>({ baseUrl: apiURL, fetch: hostedFetch });


export const HEADER_TO_USE: Record<string, string> = {
    "Content-Type": "application/json"
}


const configObj = getConfigFromHtmlFile()

function isJsonString(str: string) {
    try {
        JSON.parse(str);
    } catch (e) {
        return false;
    }
    return true;
}

const authMiddleware: Middleware = {
    async onRequest({ request}) {
        Object.entries(HEADER_TO_USE).forEach(([key, value]) => {
            request.headers.set(key, value)
        })
        const authorization = authHeader(configObj)
        if (authorization) {
            request.headers.set('Authorization', authorization)
        }
        return request;
    },
    async onResponse({ response }) {
        if (!response.ok) {
            if (response.body != null) {
                const textData = await response.text()
                if (isJsonString(textData)) {
                    const e = JSON.parse(textData)
                    // @ts-ignore
                    enqueueSnackbar(i18n.t(e.errorCode, e.arguments), {variant: 'error'})
                    throw new APIError(e)
                } else {
                    throw new Error("Request failed: " + response.body === null? response.statusText: textData);
                }
            }
        }
        return response;
    },
};

client.use(authMiddleware)

export const $api = createTanstackQueryClient(client);
