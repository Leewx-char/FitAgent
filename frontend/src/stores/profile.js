import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getProfile, createProfile, updateProfile } from '@/api/profile'

export const useProfileStore = defineStore('profile', () => {
  const profile = ref(null)
  const hasProfile = ref(false)

  async function fetchProfile() {
    try {
      const res = await getProfile()
      profile.value = res.data.data
      hasProfile.value = true
    } catch (err) {
      if (err.response?.status === 404) {
        hasProfile.value = false
        profile.value = null
      } else {
        throw err
      }
    }
  }

  async function saveProfile(data) {
    if (hasProfile.value) {
      const res = await updateProfile(data)
      profile.value = res.data.data
    } else {
      const res = await createProfile(data)
      profile.value = res.data.data
      hasProfile.value = true
    }
  }

  return { profile, hasProfile, fetchProfile, saveProfile }
})
