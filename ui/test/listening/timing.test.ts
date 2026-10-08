import {beforeEach, describe, expect, it, vi} from 'vitest'
import {passageAt} from '../../src/utils/companion'
import {startAudioPlayer} from '../../src/utils/audioPlayer'
import useAudioPlayer from '../../src/store/AudioPlayerSlice'

describe('source clock',()=>{
    beforeEach(()=>{
        vi.restoreAllMocks()
        document.body.innerHTML='<audio id="audio-player"></audio>'
        vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{})
        vi.spyOn(HTMLMediaElement.prototype,'load').mockImplementation(()=>{})
        vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue()
        useAudioPlayer.setState({pendingSeek:undefined,metadata:undefined,playbackError:undefined})
    })
    it('keeps requested position while metadata is unavailable, then seeks',async()=>{
        const audio=document.querySelector('audio')!
        Object.defineProperty(audio,'readyState',{value:0,configurable:true})
        await startAudioPlayer('https://example.test/a.mp3',600)
        expect(useAudioPlayer.getState().pendingSeek).toBe(600)
        Object.defineProperty(audio,'readyState',{value:1,configurable:true})
        Object.defineProperty(audio,'duration',{value:900,configurable:true})
        audio.dispatchEvent(new Event('loadedmetadata'))
        expect(audio.currentTime).toBe(600)
        expect(useAudioPlayer.getState().metadata?.currentTime).toBe(600)
        expect(useAudioPlayer.getState().pendingSeek).toBeUndefined()
    })
    it('a new episode cancels a previous pending seek',async()=>{
        const audio=document.querySelector('audio')!
        await startAudioPlayer('https://example.test/a.mp3',600)
        await startAudioPlayer('https://example.test/b.mp3',20)
        Object.defineProperty(audio,'readyState',{value:1,configurable:true})
        Object.defineProperty(audio,'duration',{value:900,configurable:true})
        audio.dispatchEvent(new Event('loadedmetadata'))
        expect(audio.currentTime).toBe(20)
    })
    it('restoring a position does not autoplay',async()=>{
        await startAudioPlayer('https://example.test/a.mp3',60,false)
        expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
    })
    it('does not claim a passage in a silent gap or past its end',()=>{
        const segments=[{start:0,end:10,text:'one'},{start:20,end:30,text:'two'}]
        expect(passageAt(segments,5)).toBe(0)
        expect(passageAt(segments,10)).toBe(-1)
        expect(passageAt(segments,20)).toBe(1)
        expect(passageAt(segments,35)).toBe(-1)
    })
})
