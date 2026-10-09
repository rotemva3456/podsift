import type {ImgHTMLAttributes} from 'react'

/** Keep a stable artwork area when a feed has no usable image. */
export function PodcastArtwork({src, alt = '', ...props}: ImgHTMLAttributes<HTMLImageElement>) {
    const fallback = `${import.meta.env.BASE_URL}default.jpg`
    return <img {...props} alt={alt} src={src || fallback} onError={event => {
        if (event.currentTarget.getAttribute('src') !== fallback) event.currentTarget.src = fallback
    }}/>
}
