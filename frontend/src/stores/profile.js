import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getProfile, createProfile, updateProfile } from '@/api/profile'

/** 提供健身档案及其读取、创建和更新操作的 Pinia store。 */
export const useProfileStore = defineStore('profile', () => {
  const profile = ref(null)
  const hasProfile = ref(false)

  /** 拉取档案；未创建时将本地状态重置为无档案。 */
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

  /** 根据是否已有档案选择创建或更新，并回写服务端返回的数据。 */
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
