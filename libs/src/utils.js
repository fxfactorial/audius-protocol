const bs58 = require('bs58')
const Web3 = require('./web3')
const axios = require('axios')

class Utils {
  static importDataContractABI (pathStr) {
    // need to specify part of path here because of https://github.com/webpack/webpack/issues/4921#issuecomment-357147299
    const importFile = require(`../data-contracts/ABIs/${pathStr}`)

    if (importFile) return importFile
    else throw new Error(`Data contract ABI not found ${pathStr}`)
  }

  static importEthContractABI (pathStr) {
    // need to specify part of path here because of https://github.com/webpack/webpack/issues/4921#issuecomment-357147299
    const importFile = require(`../eth-contracts/ABIs/${pathStr}`)

    if (importFile) return importFile
    else throw new Error(`Eth contract ABI not found ${pathStr}`)
  }

  static utf8ToHex (utf8Str) {
    return Web3.utils.utf8ToHex(utf8Str)
  }

  static padRight (hexStr, size) {
    return Web3.utils.padRight(hexStr, size)
  }

  static hexToUtf8 (hexStr) {
    return Web3.utils.hexToUtf8(hexStr)
  }

  static keccak256 (utf8Str) {
    return Web3.utils.keccak256(utf8Str)
  }

  static isBN (number) {
    return Web3.utils.isBN(number)
  }

  static toBN (number) {
    return new Web3.utils.BN(number)
  }

  static checkStrLen (str, maxLen, minLen = 1) {
    if (str === undefined || str === null || str.length > maxLen || str.length < minLen) {
      throw new Error(`String must be between ${minLen}-${maxLen} characters`)
    }
  }

  static async wait (milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds))
  }

  // Regular expression to check if endpoint is a FQDN. https://regex101.com/r/kIowvx/2
  static isFQDN (url) {
    let FQDN = new RegExp(/(?:^|[ \t])((https?:\/\/)?(?:localhost|[\w-]+(?:\.[\w-]+)+)(:\d+)?(\/\S*)?)/gm)
    return FQDN.test(url)
  }

  // Function to check if the endpont/health_check returns JSON object [ {'healthy':true} ]
  static async isHealthy (url) {
    try {
      let response = await axios.get(url + '/health_check')
      return response.data.healthy
    } catch (error) {
      return false
    }
  }

  static formatOptionalMultihash (multihash) {
    if (multihash) {
      return this.decodeMultihash(multihash).digest
    } else {
      return this.utf8ToHex('')
    }
  }

  static decodeMultihash (multihash) {
    const base16Multihash = bs58.decode(multihash)
    return {
      digest: `0x${base16Multihash.slice(2).toString('hex')}`,
      hashFn: parseInt(base16Multihash[0]),
      size: parseInt(base16Multihash[1])
    }
  }

  static parseDataFromResponse (response) {
    if (!response || !response.data) return null

    let obj = response.data

    // adapted from https://github.com/jashkenas/underscore/blob/master/underscore.js _.isEmpty function
    if (obj == null) return null
    if ((Array.isArray(obj) || typeof (obj) === 'string') && obj.length === 0) return null
    if (Object.keys(obj).length === 0) return null

    return obj
  }

  static async configureWeb3 (web3Provider, chainNetworkId, requiresAccount = true) {
    const web3Instance = new Web3(web3Provider)

    try {
      const networkId = await web3Instance.eth.net.getId()
      if (chainNetworkId && networkId.toString() !== chainNetworkId) {
        return false
      }
      if (requiresAccount) {
        const accounts = await web3Instance.eth.getAccounts()
        if (!accounts || accounts.length < 1) {
          return false
        }
      }
    } catch (e) {
      return false
    }

    return web3Instance
  }
}

module.exports = Utils
